# vhs9-hw-compat usage:

check-hw-compat.py [-h] [-t VERSION] [-e] [-R] [-j] [-K]

optional arguments:
  -h, --help            show this help message and exit
  -t VERSION, --target-version VERSION
                        Assume upgrade to OS of version VERSION (default: 9)
  -e, --show-entries    Show entries from deprecation database (default:
                        False)
  -R, --hide-reason     Hide reason why device is not compatible (default:
                        False)
  -j, --json            Format output as json (default: False)
  -K, --skip-kmod       Do not use kmod indexes to check if device has driver
                        (default: False)
