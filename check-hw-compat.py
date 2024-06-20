#!/usr/bin/python3

import argparse
from enum import Enum
import fnmatch
import itertools
import json
import os
from pprint import pformat
import re
from subprocess import run, PIPE, DEVNULL
import sys


COMPAT_DB_FILENAME = 'device_driver_deprecation_data.json'
EXCEPTIONS_DB_FILENAME = 'device_driver_exceptions.json'
KMOD_INDEX_DIR = './kmod-idx'


class KMod:
    def __init__(self, index_dir):
        from ctypes import CDLL, c_void_p, c_char_p, c_int, byref

        lib = CDLL('libkmod.so.2')

        ctx_t = c_void_p

        kmod_new = lib.kmod_new
        kmod_new.argtypes = (c_char_p, c_void_p)
        kmod_new.restype = ctx_t

        kmod_load_resources = lib.kmod_load_resources
        kmod_load_resources.argtypes = (ctx_t,)
        kmod_load_resources.restype = c_int

        kmod_module_new_from_lookup = lib.kmod_module_new_from_lookup
        kmod_module_new_from_lookup.argtypes = (ctx_t, c_char_p, c_void_p)
        kmod_module_new_from_lookup.restype = c_int

        kmod_module_unref_list = lib.kmod_module_unref_list
        kmod_module_unref_list.argtypes = (c_void_p,)
        kmod_module_unref_list.restype = c_int

        null = c_void_p()
        ctx = kmod_new(index_dir.encode(), byref(null))
        if not ctx:
            raise Exception('kmod_new() failed')

        load_err = kmod_load_resources(ctx)
        if load_err < 0:
            raise Exception('kmod_load_resources() failed')

        self._make_mod_list = c_void_p
        self._destroy_mod_list = kmod_module_unref_list
        self._lookup = lambda name, mod_list: kmod_module_new_from_lookup(
            ctx, name.encode(), byref(mod_list))

    def has_module(self, name):
        mod_list = self._make_mod_list()
        if self._lookup(name, mod_list) < 0:
            raise Exception('Module lookup failed')
        rv = bool(mod_list)
        self._destroy_mod_list(mod_list)
        return rv


class Status(Enum):
    ok = 0
    removed = 1
    unmaintained = 2


def normalize_module_name(name):
    return name.replace('-', '_')


class Device:
    def __init__(self, sysfs_path, modalias, modules):
        self.sysfs_path = sysfs_path
        self.modalias = modalias
        self.modules = [normalize_module_name(mod) for mod in modules]

        mod_symlink = os.path.join(self.sysfs_path, 'driver/module')
        try:
            mod_rel_path = os.readlink(mod_symlink)
        except FileNotFoundError:
            self.current_module = None
        else:
            self.current_module = os.path.basename(mod_rel_path)


class PCIDevice(Device):
    def __init__(self, attrs, pci_id):
        sysfs_path = os.path.join('/sys/bus/pci/devices', attrs['Slot'])
        with open(os.path.join(sysfs_path, 'modalias')) as f:
            modalias = f.read().rstrip()

        Device.__init__(self, sysfs_path, modalias, attrs['Module'])

        self.attrs = attrs
        self.pci_id = pci_id

    def __str__(self):
        return ' '.join(self.attrs[k] for k in ('Slot', 'Vendor', 'Device'))

    __repr__ = __str__


class MiscDevice(Device):
    def __str__(self):
        return self.sysfs_path

    __repr__ = __str__


def get_pci_devices():
    id_tags = ('Vendor', 'Device', 'SVendor', 'SDevice')
    id_re = re.compile(r'^\s*(.*) \[([0-9a-fA-F]+)\]$')
    def parse_id(x):
        if x is None:
            return 0, ''

        desc, id_num = id_re.match(x).groups()
        return desc, int(id_num, 16)

    tag_re = re.compile(r'^(\w+):\t(.*)$', re.MULTILINE)
    multi_value_tags = ('Module',)

    p = run(['lspci', '-vmmknnD'], stdout=PIPE, check=True)
    rv = []
    for block in p.stdout.decode().split('\n\n')[:-1]:
        attrs = {}
        for tag in multi_value_tags:
            attrs[tag] = []
        for tag, val in tag_re.findall(block):
            if tag in multi_value_tags:
                attrs[tag].append(val)
            else:
                attrs[tag] = val

        vendor_desc, vendor_id = parse_id(attrs['Vendor'])
        device_desc, device_id = parse_id(attrs['Device'])
        sub_vendor_desc, sub_vendor_id = parse_id(attrs.get('SVendor'))
        sub_device_desc, sub_device_id = parse_id(attrs.get('SDevice'))

        attrs['Vendor'] = vendor_desc
        attrs['Device'] = device_desc
        attrs['SVendor'] = sub_vendor_desc
        attrs['SDevice'] = sub_device_desc

        rv.append(PCIDevice(
            attrs,
            (vendor_id, device_id, sub_vendor_id, sub_device_id),
        ))

    return rv


def get_misc_devices(loaded_modules):
    p = run(['find', '/sys/devices', '-type', 'f', '-name', 'modalias'],
            stdout=PIPE, check=True)

    rv = []
    cache = {}
    for filename in p.stdout.decode().splitlines():
        with open(filename) as f:
            modalias = f.read().rstrip()

        if modalias.startswith('pci:'):
            continue

        if modalias.startswith('x86cpu:'):
            continue

        if modalias in cache:
            modules = cache[modalias]
        else:
            p = run(['modprobe', '--resolve-alias', modalias],
                    stdout=PIPE, stderr=DEVNULL)
            if p.returncode == 0:
                modules = set(p.stdout.decode().splitlines())
            else:
                modules = set()
            cache[modalias] = modules

        dev = MiscDevice(
            os.path.dirname(filename),
            modalias,
            modules
        )
        if (dev.current_module is None and len(modules) == 1
                and modules & loaded_modules):
            dev.current_module = list(modules)[0]
        rv.append(dev)

    return rv


def get_loaded_modules():
    p = run(['lsmod'], stdout=PIPE, check=True)
    lines = p.stdout.decode().splitlines()
    return {normalize_module_name(l.split()[0]) for l in lines[1:]}


def get_all_modules():
    return {normalize_module_name(m) for m in os.listdir('/sys/module')}


def get_pci_id_entry_map(compat_db):
    def parse_pci_id(pci_id):
        return tuple(int(x, 16) for x in pci_id.split(':'))

    return {
        parse_pci_id(ent['device_id']) : ent
        for ent in compat_db
        if ent['device_type'] == 'pci' and ent['device_id']
    }


def get_module_entry_map(compat_db):
    return {
        normalize_module_name(ent['driver_name']) : ent
        for ent in compat_db
        if not ent['device_id']
    }


def match_devices(loaded_modules, pci_id_entry_map, mod_entry_map):
    pci_devs = get_pci_devices()
    check_mod_devs = get_misc_devices(loaded_modules)

    dev_entries = []
    dev_modules = set()
    for dev in pci_devs:
        dev_modules.update(dev.modules)

        for idx in (4, 3, 2):
            ent = pci_id_entry_map.get(dev.pci_id[:idx])
            if ent is not None:
                dev_entries.append((dev, None, ent))
                break
        else:
            check_mod_devs.append(dev)

    for dev in check_mod_devs:
        dev_modules.update(dev.modules)
        dev_entries.append((
            dev,
            dev.current_module,
            mod_entry_map.get(dev.current_module)
        ))

    return dev_entries, dev_modules


def get_status(entry, target_version):
    if entry is None:
        return Status.ok
    elif target_version not in entry['available_in_rhel']:
        return Status.removed
    elif target_version not in entry['maintained_in_rhel']:
        return Status.unmaintained
    else:
        return Status.ok


def get_incompatible_devices(dev_entries, kmod, builtin_modules, target_version):
    rv = []
    def append(status, message):
        details = (message, ent)
        rv.append(('device', dev, status.name, details))

    for dev, mod, ent in dev_entries:
        if ent is None:
            if dev.current_module is None:
                continue
            if not dev.modules and dev.current_module not in builtin_modules:
                continue
            if kmod is None or kmod.has_module(dev.modalias):
                continue

            append(Status.removed,
                   'No module for {!r}'.format(dev.modalias))
            continue

        st = get_status(ent, target_version)
        if st == Status.ok:
            continue

        if mod is None:
            msg = 'Device with ID {} is {}'.format(ent['device_id'], st.name)
        else:
            msg = 'Module {} is {}'.format(mod, st.name)
        append(st, msg)

    return rv


def get_incompatible_modules(mod_entries, target_version):
    rv = []
    for mod, ent in mod_entries:
        st = get_status(ent, target_version)
        if st == Status.ok:
            continue

        details = ('', ent)
        rv.append(('module', mod, st.name, details))

    return rv


def get_incompatible(compat_db, exc_pred, skip_kmod, target_version):
    pci_id_entry_map = get_pci_id_entry_map(compat_db)
    mod_entry_map = get_module_entry_map(compat_db)

    modules = get_loaded_modules()
    dev_entries, dev_modules = match_devices(modules, pci_id_entry_map, mod_entry_map)
    mod_entries = [(
        mod,
        mod_entry_map.get(mod)
    ) for mod in modules - dev_modules]

    kmod = None if skip_kmod else KMod(
        os.path.join(KMOD_INDEX_DIR))
    builtin_modules = get_all_modules() - modules

    def check_exc(args):
        obj_type, obj, status, details = args
        name = obj if obj_type == 'module' else obj.modalias
        return not exc_pred(name)

    return list(filter(
        check_exc,
        itertools.chain(
            get_incompatible_devices(dev_entries, kmod, builtin_modules, target_version),
            get_incompatible_modules(mod_entries, target_version),
        )
    ))


def load_compat_db():
    with open(COMPAT_DB_FILENAME) as f:
        return json.load(f)['data']


def load_exc_db():
    with open(EXCEPTIONS_DB_FILENAME) as f:
        patterns = json.load(f)

    if not patterns:
        return lambda name: False

    patterns_regexp = '|'.join(map(fnmatch.translate, patterns))
    return re.compile(patterns_regexp).match


def print_plain(incompatible, show_reason, show_entries):
    indent = '    '
    def fmt_ent(ent):
        s = pformat(ent)
        return ''.join(indent + ln for ln in s.splitlines(True))

    for obj_type, obj, status, (msg, ent) in incompatible:
        print(f'{obj_type:<6} {status:<12} {obj}')
        if show_reason and msg:
            print(f'{indent}{msg}')
        if show_entries:
            print(fmt_ent(ent), '\n')


def print_json(incompatible, show_reason, show_entries):
    data = []
    for obj_type, obj, status, (msg, ent) in incompatible:
        d = {
            'type': obj_type,
            'object': str(obj),
            'status': status,
        }
        if show_reason:
            d['reason'] = msg
        if show_entries:
            d['entry'] = ent
        data.append(d)

    json.dump(data, sys.stdout)


def print_incompatible(incompatible, show_reason, show_entries, json):
    (print_json if json else print_plain)(incompatible, show_reason, show_entries)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('-t', '--target-version', type=int, default=9,
                        metavar='VERSION',
                        help='Assume upgrade to OS of version VERSION')
    parser.add_argument('-e', '--show-entries', action='store_true',
                        help='Show entries from deprecation database')
    parser.add_argument('-R', '--hide-reason', action='store_true',
                        help='Hide reason why device is not compatible')
    parser.add_argument('-j', '--json', action='store_true',
                        help='Format output as json')
    parser.add_argument('-K', '--skip-kmod', action='store_true',
                        help='Do not use kmod indexes to check if device has driver')

    args = parser.parse_args()
    print_incompatible(
        get_incompatible(
            load_compat_db(),
            load_exc_db(),
            args.skip_kmod,
            args.target_version),
        show_reason=not args.hide_reason,
        show_entries=args.show_entries,
        json=args.json
    )
