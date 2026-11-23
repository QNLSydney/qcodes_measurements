# qcodes-measurements

Measurement helpers and live plotting for QCoDeS.

## Installation

Requires Python 3.12 or newer.

```sh
uv sync
```

To add to a measurement package, you can use the fully resolved URL:
```
uv add https://github.com/QNLSydney/qcodes_measurements.git
```

## Measurements

Create an experiment and run a sweep with live plotting:

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
```

Using the same experiment, sample random voltage noise for ten seconds:

```python
import numpy as np
from qcodes_measurements import sweep_time

rng = np.random.default_rng()
noise = Parameter("noise", unit="V", get_cmd=lambda: rng.normal(scale=1e-3))
dataset, windows, colorbars = sweep_time(noise, delay=0.1, until=10, save=False)
```

Use `do0d` for a single measurement or `do2d` for a two-dimensional sweep. Pass
`plot=False` to disable plotting or `save=False` to skip saving figures. Plot an
existing dataset with `qcodes_measurements.plot.plot_dataset(dataset)`.

To overlap the remote plotting process's startup with instrument setup, call this
from the main thread near the start of your session:

```python
from qcodes_measurements.plot import start_remote

start_remote(background=True)
```

The first live plot waits for the remote imports and setup to finish if needed.

## Devices

Define named gates and ohmic contacts in `device.yaml`:

```yaml
instruments:
  device:
    type: qcodes_measurements.device.Device
    init:
      default_gate_limit: [-2.5, 0]
      default_ohmic_limit: [-0.002, 0.002]
      gates:
        G1:
          source: mdac.ch01
          label: Gate 1
          limits: [-1.5, 0]
      ohmics:
        S:
          source: mdac.ch02
          label: Source bias
          scale: 100  # 100:1 voltage divider on the ohmic source
```

Combine this with your instrument definitions in `instrument.yaml`:

```python
from qcodes.station import Station

station = Station(config_file=["instrument.yaml", "device.yaml"])
mdac = station.load_mdac()
device = station.load_device()

# And set voltages on gates
device.G1(-0.1)
```

## Instrument mirrors

Run each mirror in its own terminal. Connect to an MDAC on `COM4` and a Yoko at
`192.168.1.50`:

```sh
uv run mdac-mirror COM4 --listen-host 127.0.0.1 --listen-port 5000
uv run yoko-mirror 192.168.1.50 --listen 127.0.0.1 --port 7655
```

Use `--log-level DEBUG` for detailed logging, or `--help` to see all options.

### Station configuration with Mirrors

Save the following as `station.yaml` to connect to the mirrors above using an
MDAC and a Yokogawa GS200:

```yaml
instruments:
  mdac:
    type: qcodes_measurements.drivers.MDAC.MDAC
    address: "ZMQ::tcp://127.0.0.1:5000"
  yoko:
    type: qcodes.instrument_drivers.yokogawa.YokogawaGS200
    address: "TCPIP0::127.0.0.1::7655::SOCKET"
```

Load the instruments in Python:

```python
from qcodes.station import Station

station = Station(config_file="station.yaml")
mdac = station.load_mdac()
yoko = station.load_yoko()
```
