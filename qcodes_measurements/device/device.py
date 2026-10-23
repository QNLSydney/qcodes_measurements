from collections.abc import Mapping, Sequence
from typing import Any, Unpack

from qcodes.instrument import ChannelList, Instrument, InstrumentBase
from qcodes.instrument.instrument_base import InstrumentBaseKWArgs, TParameter
from qcodes.parameters import ParameterBase

from ..drivers import MDAC
from .bb import BBChan
from .channel_wrapper import ChannelWrapper
from .gate import (
    BBGateWrapper,
    BBOhmicWrapper,
    Gate,
    GateWrapper,
    MDACGateWrapper,
    MDACOhmicWrapper,
    Ohmic,
    OhmicWrapper,
    limit_validator,
)
from .states import ConnState

type ContactDefinitions = Mapping[str, Mapping[str, Any]]


class Device(Instrument):
    """Logical device whose contacts refer to already initialized voltage sources.

    ``gates`` and ``ohmics`` map contact names to constructor options. ``source``
    may be a channel object or a dotted path such as ``"mdac.ch01"``; the root
    instrument must already exist in the QCoDeS registry. Other options include
    ``label``, ``unit``, ``scale``, ``offset``, ``limits``, and ``default_value``.

    ``default_gate_limit`` and ``default_ohmic_limit`` supply [lower, upper]
    limits for newly added contacts, expressed in each contact's units before
    scale/offset. Per-contact ``limits`` or a Python ``vals`` validator override
    the defaults. ``None`` leaves only the source voltage's own validation.

    Loading does not set voltages unless ``default_value`` (or the QCoDeS alias
    ``initial_value``) is explicitly supplied. Requested connection states are
    accepted without writes if already correct; changing them requires the
    source voltage to be exactly zero. Analog voltage changes use hardware or
    software ramps. ``rate`` and ``max_step`` use source volts/s and volts.
    """

    def __init__(
        self,
        name: str,
        *,
        gates: ContactDefinitions | None = None,
        ohmics: ContactDefinitions | None = None,
        default_gate_limit: Sequence[float] | None = (-2.5, 0),
        default_ohmic_limit: Sequence[float] | None = (-2e-3, 2e-3),
        **kwargs: Unpack[InstrumentBaseKWArgs],
    ) -> None:
        if Instrument.exist(name):
            raise KeyError(f"Another instrument has the name: {name}")
        limit_validator(default_gate_limit)
        limit_validator(default_ohmic_limit)
        self.default_gate_limit = default_gate_limit
        self.default_ohmic_limit = default_ohmic_limit
        super().__init__(name, **kwargs)
        self.gates = self.add_submodule("gates", ChannelList(self, "gates", GateWrapper))
        self.ohmics = self.add_submodule("ohmics", ChannelList(self, "ohmics", OhmicWrapper))
        self._configure_contacts(gates, ohmics)

    def get_idn(self) -> dict[str, str | None]:
        return dict(
            vendor="qcodes_measurements", model=type(self).__name__, serial=None, firmware=None
        )

    @staticmethod
    def resolve_source(source: str | InstrumentBase) -> InstrumentBase:
        if isinstance(source, str):
            root, *path = source.split(".")
            resolved: Any = Instrument.find_instrument(root)
            for part in path:
                resolved = getattr(resolved, part)
            source = resolved
        if not isinstance(source, InstrumentBase):
            raise TypeError("source must resolve to an instrument or channel")
        return source

    def _configured_class(self, kind, parameter_type):
        cls = Gate if kind == "gates" else Ohmic
        if parameter_type not in (None, cls.__name__, f"qcodes_measurements.device.{cls.__name__}"):
            raise ValueError(f"Unsupported {kind} type {parameter_type!r}")
        return cls

    def _configure_contacts(self, gates, ohmics):
        # Resolve every dependency before applying any requested initial values.
        prepared = []
        names = set(self.parameters) | set(self.submodules)
        for kind, definitions in (("gates", gates), ("ohmics", ohmics)):
            for name, definition in (definitions or {}).items():
                if name in names or hasattr(self, name):
                    raise ValueError(f"Duplicate or reserved contact name {name!r}")
                names.add(name)
                options = dict(definition)
                cls = self._configured_class(kind, options.pop("type", None))
                try:
                    options["source"] = self.resolve_source(options["source"])
                except (KeyError, AttributeError, TypeError) as exc:
                    raise ValueError(
                        f"{self.name}.{kind}.{name}: cannot resolve source {definition.get('source')!r}"
                    ) from exc
                if "limits" in options:
                    limit_validator(options["limits"])
                prepared.append((name, cls, options))
        for name, cls, options in prepared:
            self._add_contact(name, cls, **options)

    def _add_contact(self, name, parameter_class, source, state=None, io_mode=None, **kwargs):
        value = kwargs.pop("default_value", None)
        initial_value = kwargs.pop("initial_value", None)
        if value is not None and initial_value is not None:
            raise ValueError("Specify only one of default_value and initial_value")
        value = initial_value if value is None else value
        state = (
            ConnState[state]
            if isinstance(state, str) and not isinstance(state, ConnState)
            else state
        )
        parameter = self.add_parameter(name, parameter_class, source=source, **kwargs)
        if value is not None:
            parameter.validate(value)
        controller = self.get_channel_controller(parameter)
        self._configure_controller(parameter, controller, state, io_mode, value)
        if value is not None:
            parameter(value)
        self._finalize_contact(parameter, io_mode)
        return parameter

    def _configure_controller(self, parameter, controller, state, io_mode, value):
        if io_mode is not None:
            raise ValueError("io_mode is only supported for digital gates")
        if state is not None and not isinstance(state, ConnState):
            raise ValueError(f"Invalid connection state {state!r}")
        if state is not None and controller.state() != state:
            if parameter.source.voltage() != 0:
                raise ValueError(
                    f"{self.name}.{parameter.name}: cannot change connection state at non-zero voltage"
                )
            controller.state(state)

    def _finalize_contact(self, parameter, io_mode):
        pass

    def add_gate(self, name, source, state=None, **kwargs):
        return self._add_contact(name, Gate, source, state=state, **kwargs)

    def add_ohmic(self, name, source, state=None, **kwargs):
        return self._add_contact(name, Ohmic, source, state=state, **kwargs)

    def add_parameter(
        self,
        name: str,
        parameter_class: type[TParameter] | None = None,
        **kwargs: Any,
    ) -> TParameter:
        if parameter_class is not None and issubclass(parameter_class, (Gate, Ohmic)):
            kwargs["source"] = self.resolve_source(kwargs["source"])
            if "limits" not in kwargs and "vals" not in kwargs:
                kwargs["limits"] = (
                    self.default_gate_limit
                    if issubclass(parameter_class, Gate)
                    else self.default_ohmic_limit
                )
        new_param = super().add_parameter(name, parameter_class=parameter_class, **kwargs)
        self.store_new_param(new_param)
        return new_param

    def store_new_param(self, new_param: ParameterBase) -> None:
        if isinstance(new_param, Gate):
            if isinstance(new_param.source, MDAC.MDACChannel):
                wrapper = MDACGateWrapper
            elif isinstance(new_param.source, BBChan):
                wrapper = BBGateWrapper
            else:
                wrapper = GateWrapper
            self.gates.append(wrapper(new_param, new_param.name))
        elif isinstance(new_param, Ohmic):
            if isinstance(new_param.source, MDAC.MDACChannel):
                wrapper = MDACOhmicWrapper
            elif isinstance(new_param.source, BBChan):
                wrapper = BBOhmicWrapper
            else:
                wrapper = OhmicWrapper
            self.ohmics.append(wrapper(new_param, new_param.name))

    def get_channel_controller(self, param):
        if isinstance(param, Gate):
            return getattr(self.gates, param.name)
        if isinstance(param, Ohmic):
            return getattr(self.ohmics, param.name)
        return None

    def lookup_source(self, source) -> ChannelWrapper | None:
        source = self.resolve_source(source)
        for controller in (*self.gates, *self.ohmics):
            if controller.gate.source is source:
                return controller
        return None
