# this is a long and ugly import, it should change once the station
# configurator will be part of main qcodes
from . station_configurator.qdev_wrappers.station_configurator import StationConfigurator


scfg = StationConfigurator('exampleConfig.yaml')
dmm1 = scfg.load_instrument('dmm1')
mock_dac = scfg.load_instrument('mock_dac')

# if you happen to have a qdac you can also change the hardware address in the
# config file and then do the following:
# watch out! the current config file will set a voltage on the qdac!
qdac = scfg.load_instrument('qdac')
# now you should be able to do
qdac.Bx(0.04)
# which should ramp up the voltage of ch02 from 0.02V (initial value) to
# 0.08V (scaling factor is 0.2)
# that is fine
qdac.Bx(0.09)
# but this will fail because it is outside the specified range
qdac.Bx(0.11)