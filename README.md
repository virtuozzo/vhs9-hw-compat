# VHS9-HW-COMPAT

`vhs9-hw-compat` is a tool that displays hardware components **unsupported** by Virtuozzo Hybrid Server 9.

## Pre-requisites

The tool requires Python 3.6 or newer.

## Usage

Clone the repository:
```
git clone https://github.com/virtuozzo/vhs9-hw-compat.git
```

Run the tool:
```
python3 vhs9-hw-compat/check-hw-compat.py
```

## Optional arguments:

* `-h, --help`:  show the help message
* `-t VERSION, --target-version VERSION`:  Assume upgrade to OS of version VERSION (default: 9)
* `-e, --show-entries`:  Show entries from deprecation database (default: False)
* `-R, --hide-reason`:  Hide reason why device is not compatible (default: False)
* `-j, --json`:  Format output as json (default: False)
* `-K, --skip-kmod`:  Do not use kmod indexes to check if device has driver (default: False)
