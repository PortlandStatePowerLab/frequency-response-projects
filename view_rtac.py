"""Browse RTAC CSV recordings and plot frequency against sample time."""

import argparse
import csv
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "Weekly Automation System"
WSL_ROOT = Path("/mnt/c/Users/remote/Desktop/Weekly Automation System")
RECORDING_NAME = re.compile(r"^\d{4}(?:-\d{2}){4}_.+\.csv$", re.IGNORECASE)
FREQUENCY_LIMITS = (59.95, 60.05)


def find_recordings(root):
    return sorted(
        (path for path in root.rglob("*.csv") if RECORDING_NAME.fullmatch(path.name)),
        key=lambda path: (path.name, str(path)),
    )


def read_recording(path):
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as source:
        rows = csv.DictReader(source)
        fields = rows.fieldnames or []
        timestamp = next(
            (field for field in fields if field.strip().casefold() == "timestamp"),
            None,
        )
        if timestamp is None:
            raise ValueError("This CSV has no Timestamp column.")
        channels = {
            field: []
            for field in fields
            if field.strip().rsplit(":", 1)[-1].casefold() in {"freq", "frequency"}
        }
        if not channels:
            raise ValueError("This CSV has no frequency column.")
        numeric = set()
        times = []
        for row in rows:
            try:
                sample_time = datetime.fromisoformat(row[timestamp].strip().replace("/", "-"))
            except (ValueError, AttributeError):
                continue
            times.append(sample_time)
            for field, values in channels.items():
                try:
                    value = float(row[field])
                except (TypeError, ValueError):
                    value = math.nan
                if math.isfinite(value):
                    numeric.add(field)
                values.append(value if math.isfinite(value) else math.nan)
    if not times or not numeric:
        raise ValueError("This CSV has no timestamped frequency samples.")
    return times, {field: values for field, values in channels.items() if field in numeric}


class Viewer:
    def __init__(self, window, root):
        global tk, ttk, filedialog, mdates
        import tkinter as tk
        from tkinter import filedialog, ttk
        import matplotlib.dates as mdates
        from matplotlib.ticker import FormatStrFormatter
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
        from matplotlib.figure import Figure

        self.window = window
        self.root = root
        self.recordings = []
        self.visible = []
        self.path = None
        self.times = []
        self.channels = {}
        self.filter = tk.StringVar()
        self.status = tk.StringVar()
        window.title("RTAC Recording Viewer")
        window.geometry("1200x760")

        pane = ttk.Panedwindow(window, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=10, pady=10)
        sidebar = ttk.Frame(pane, padding=(0, 0, 10, 0), width=330)
        plot_area = ttk.Frame(pane)
        pane.add(sidebar, weight=0)
        pane.add(plot_area, weight=1)

        ttk.Button(sidebar, text="Choose recording folder", command=self.choose_folder).pack(fill="x")
        ttk.Label(sidebar, text="Filter by date, filename, or folder").pack(anchor="w", pady=(10, 2))
        self.search = ttk.Entry(sidebar, textvariable=self.filter)
        self.search.pack(fill="x")
        self.filter.trace_add("write", self.refresh_list)
        self.files = self.make_list(sidebar, height=17)
        self.files.bind("<<ListboxSelect>>", self.load_selected)

        self.figure = Figure(constrained_layout=True)
        self.frequency_formatter = FormatStrFormatter("%.3f")
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_area)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(self.canvas, plot_area)
        ttk.Label(window, textvariable=self.status, padding=(10, 0, 10, 8), wraplength=1160).pack(fill="x")
        self.scan(root)
        self.search.focus_set()

    @staticmethod
    def make_list(parent, **options):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=(2, 0))
        vertical = ttk.Scrollbar(frame)
        horizontal = ttk.Scrollbar(frame, orient="horizontal")
        items = tk.Listbox(
            frame, exportselection=False, width=37, yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set, **options
        )
        vertical.config(command=items.yview)
        horizontal.config(command=items.xview)
        items.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        return items

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.root if self.root.is_dir() else Path.home())
        if folder:
            self.scan(Path(folder))

    def scan(self, root):
        self.root = root
        self.path = None
        self.recordings = find_recordings(root)
        self.figure.clear()
        self.canvas.draw()
        self.filter.set("")

    def refresh_list(self, *_):
        query = self.filter.get().strip().casefold()
        self.files.delete(0, tk.END)
        self.visible = []
        for path in self.recordings:
            relative = path.relative_to(self.root)
            label = f"{path.name}  |  {relative.parent}"
            if query in label.casefold():
                self.visible.append(path)
                self.files.insert(tk.END, label)
        if self.visible:
            self.status.set(f"{len(self.visible):,} of {len(self.recordings):,} CSV files. Select a recording to plot.")
        else:
            self.status.set(f"No matching recordings in {self.root}")

    def load_selected(self, _event):
        selected = self.files.curselection()
        if not selected:
            return
        self.path = self.visible[selected[0]]
        try:
            self.times, self.channels = read_recording(self.path)
        except (OSError, UnicodeError, ValueError, csv.Error) as error:
            self.figure.clear()
            self.canvas.draw()
            self.status.set(f"Could not read {self.path.name}: {error}")
            return
        self.draw()

    def draw(self):
        self.figure.clear()
        axis = self.figure.subplots()
        for field, values in self.channels.items():
            axis.plot(self.times, values, linewidth=0.8, label=field)
        if len(self.channels) > 1:
            axis.legend()
        axis.set_ylabel("Frequency (Hz)")
        axis.set_ylim(*FREQUENCY_LIMITS)
        axis.yaxis.set_major_formatter(self.frequency_formatter)
        if min(self.times) < max(self.times):
            axis.set_xlim(min(self.times), max(self.times))
        axis.grid(alpha=0.25)
        locator = mdates.AutoDateLocator()
        axis.xaxis.set_major_locator(locator)
        axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        axis.set_xlabel("Recording timestamp")
        self.figure.suptitle(self.path.name)
        self.status.set(f"{recording_span(self.times)}   |   {self.path}")
        self.canvas.draw()
        self.canvas.toolbar.update()


def recording_span(times):
    start, end = min(times), max(times)
    return (
        f"{len(times):,} samples | "
        f"{start.isoformat(sep=' ', timespec='milliseconds')} to "
        f"{end.isoformat(sep=' ', timespec='milliseconds')} | "
        f"{(end - start).total_seconds() / 60:.2f} minutes"
    )


def frequency_svg(times, channels):
    low, high = FREQUENCY_LIMITS
    start, end = min(times), max(times)
    duration = (end - start).total_seconds() or 1
    colors = ("#1760a5", "#bc471b", "#227747", "#763e9b")
    parts = ['<svg viewBox="0 0 1000 400" role="img" aria-label="Frequency versus time">']
    parts.append('<defs><clipPath id="plot-area"><rect x="90" y="20" width="890" height="320"/></clipPath></defs>')
    for index in range(6):
        y = 20 + index * 64
        hz = high - index * (high - low) / 5
        parts.append(f'<path d="M90 {y}H980" stroke="#ddd"/><text x="82" y="{y + 4}" text-anchor="end">{hz:.3f}</text>')
    for index in range(6):
        fraction = index / 5
        x = 90 + 890 * fraction
        time = start + timedelta(seconds=(end - start).total_seconds() * fraction)
        parts.append(
            f'<path d="M{x} 20V340" stroke="#ddd"/>'
            f'<text x="{x}" y="365" text-anchor="middle">{time:%H:%M:%S}</text>'
        )
    for index, (name, series) in enumerate(channels.items()):
        color = colors[index % len(colors)]
        points = []
        draw = False
        for time, value in zip(times, series):
            if not math.isfinite(value):
                draw = False
                continue
            x = 90 + 890 * (time - start).total_seconds() / duration
            y = 20 + 320 * (high - value) / (high - low)
            points.append(f'{"L" if draw else "M"}{x:.2f} {y:.2f}')
            draw = True
        parts.append(f'<path d="{" ".join(points)}" stroke="{color}" fill="none" clip-path="url(#plot-area)"><title>{escape(name)}</title></path>')
    parts.append('<text x="90" y="392">Recording timestamp</text><text x="980" y="392" text-anchor="end">Frequency (Hz)</text></svg>')
    return "".join(parts)


def serve_viewer(root, port):
    recordings = find_recordings(root)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            if url.path != "/":
                self.send_error(404)
                return
            query = parse_qs(url.query)
            search = query.get("q", [""])[0]
            try:
                selected = int(query.get("file", ["-1"])[0])
            except ValueError:
                selected = -1
            options = []
            for index, path in enumerate(recordings):
                label = str(path.relative_to(root))
                if search.casefold() in label.casefold():
                    mark = " selected" if index == selected else ""
                    options.append(f'<option value="{index}"{mark}>{escape(label)}</option>')
            chart = "<p>Select a recording to plot.</p>"
            if 0 <= selected < len(recordings):
                path = recordings[selected]
                try:
                    times, channels = read_recording(path)
                    chart = f"<h2>{escape(path.name)}</h2><p>{recording_span(times)}</p>"
                    chart += frequency_svg(times, channels)
                except (OSError, UnicodeError, ValueError, csv.Error) as error:
                    chart = f"<p>{escape(str(error))}</p>"
            page = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RTAC Recording Viewer</title>
<style>body{{font:16px system-ui;margin:2rem auto;max-width:1050px;padding:0 1rem}}
select{{width:100%;margin-top:.5rem}} svg{{width:100%;font:12px system-ui}} input,button{{font:inherit}}</style>
<h1>RTAC Recording Viewer</h1>
<form><label>Filter by date, filename, or folder <input name="q" value="{escape(search, quote=True)}"></label>
<button>Filter</button></form>
<form><input type="hidden" name="q" value="{escape(search, quote=True)}">
<label>Recordings ({len(options):,})<select name="file" size="8">{"".join(options)}</select></label>
<button>Plot frequency</button></form>{chart}</html>"""
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"{len(recordings):,} recordings. Open http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", type=Path)
    parser.add_argument("--browser", action="store_true", help="serve plots on localhost")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    root = (args.folder or (WSL_ROOT if WSL_ROOT.is_dir() else DEFAULT_ROOT)).expanduser().resolve()
    if not root.is_dir():
        parser.error(f"recording folder does not exist: {root}")
    if args.browser or WSL_ROOT.is_dir():
        serve_viewer(root, args.port)
        return
    import tkinter

    window = tkinter.Tk()
    Viewer(window, root)
    window.mainloop()


if __name__ == "__main__":
    main()
