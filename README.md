# qcodes-measurements

Live plotting and measurement helpers for QCoDeS, using pyqtgraph in a separate
PySide6 process so plotting can continue while a measurement runs.

Requires Python 3.12 or newer. The current dependency baseline is QCoDeS 0.61.1,
PySide6 6.12, and pyqtgraph 0.14.

Install the project and development tools with uv:

```sh
uv sync
```

A measurement uses the usual QCoDeS parameters and experiment setup:

```python
from qcodes.dataset import initialise_or_create_database_at, new_experiment
from qcodes.parameters import Parameter
from qcodes_measurements import do1d

initialise_or_create_database_at("measurements.db")
new_experiment("example", sample_name="sample")

voltage = Parameter("voltage", unit="V", set_cmd=None, initial_value=0)
current = Parameter("current", unit="A", get_cmd=lambda: voltage() / 1e6)

dataset, windows, colorbars = do1d(
    voltage, -0.1, 0.1, 101, 0.01, current, save=False
)
window = windows[0]
```

`do0d`, `do1d`, `do2d`, and `sweep_time` accept `plot`, `plot_params`, `append`,
`stack`, `annotation`, and `save` options. With plotting enabled, the second return
value contains the live plot window. Use `plot=False` to keep QCoDeS's non-plotting
return values. Figures are saved to `figures/` by default; `save=False` disables
saving. `sweep_time` accepts a positive `delay` and an optional `until` duration in
seconds; omit `until` to measure until interrupted.

To plot an existing dataset, use `qcodes_measurements.plot.plot_dataset(dataset)`.
The bundled MDAC driver is available as `qcodes_measurements.drivers.MDAC`;
device wrappers also support an existing top-level `MDAC` installation.

Run development checks with uv:

```sh
uv run ruff check qcodes_measurements tests
uv run ruff format --check qcodes_measurements tests
uv run ty check
uv run pylint qcodes_measurements
QT_QPA_PLATFORM=offscreen uv run python -m pytest -W error tests
```

`QT_QPA_PLATFORM=offscreen` runs Qt without displaying windows.
