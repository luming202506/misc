#!/usr/bin/env python3
"""
gem5 script using the built-in RiscvBoard library.
Proper setup for RISC-V full system simulation.

Usage:
    ./gem5.opt run_riscv_library.py
"""

import os
import subprocess
from pathlib import Path

# Import from specific modules
from gem5.components.boards.riscv_board import RiscvBoard
from gem5.components.cachehierarchies.classic.private_l1_shared_l2_cache_hierarchy import PrivateL1SharedL2CacheHierarchy
from gem5.components.memory.single_channel import SingleChannelDDR4_2400
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.components.processors.cpu_types import CPUTypes
from gem5.isas import ISA
from gem5.resources.resource import KernelResource, DiskImageResource, BootloaderResource

import m5


# Configuration
DISK_IMAGE = '/home/luming/disk.qcow2'
KERNEL_PATH = '/home/luming/linux/vmlinux'
BOOTLOADER_PATH = '/home/luming/opensbi/build/platform/generic/firmware/fw_jump.elf'


def convert_qcow2(qcow2_path):
    """Convert qcow2 to raw format."""
    raw_path = qcow2_path.replace('.qcow2', '.raw')
    if not os.path.exists(raw_path) or \
       os.path.getmtime(raw_path) < os.path.getmtime(qcow2_path):
        print(f"[Convert] {qcow2_path} -> {raw_path}")
        subprocess.run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'raw',
                        qcow2_path, raw_path], check=True)
        print(f"[Convert] Done! Size: {os.path.getsize(raw_path) // (1024*1024)} MB")
    else:
        print(f"[Cache] Using cached: {raw_path}")
    return raw_path


print("[Setup] Creating RISC-V board...")

# Convert disk image
if DISK_IMAGE.endswith('.qcow2'):
    raw_disk = convert_qcow2(DISK_IMAGE)
else:
    raw_disk = DISK_IMAGE

# Create cache hierarchy
cache_hierarchy = PrivateL1SharedL2CacheHierarchy(
    l1d_size="64KiB",
    l1i_size="64KiB",
    l2_size="1MiB",
)

# Create memory
memory = SingleChannelDDR4_2400(size="4GiB")

# Create processor
processor = SimpleProcessor(
    cpu_type=CPUTypes.TIMING,
    num_cores=1,
    isa=ISA.RISCV,
)

# Create RISC-V board
board = RiscvBoard(
    clk_freq="1.4GHz",
    processor=processor,
    memory=memory,
    cache_hierarchy=cache_hierarchy,
)

# Prepare resources
kernel = KernelResource(KERNEL_PATH) if KERNEL_PATH and os.path.exists(KERNEL_PATH) else None
disk = DiskImageResource(raw_disk, raw_image=True) if raw_disk and os.path.exists(raw_disk) else None
bootloader = BootloaderResource(BOOTLOADER_PATH) if BOOTLOADER_PATH and os.path.exists(BOOTLOADER_PATH) else None

# Set workload using the proper method
if kernel and disk:
    print(f"[Setup] Kernel: {KERNEL_PATH}")
    print(f"[Setup] Disk: {raw_disk}")

    if bootloader:
        print(f"[Setup] Bootloader: {BOOTLOADER_PATH}")
        board.set_kernel_disk_workload(
            kernel=kernel,
            disk_image=disk,
            bootloader=bootloader,
            kernel_args=["root=/dev/vda", "rw", "console=ttyS0"],
        )
    else:
        print("[WARN] No bootloader - RISC-V requires BBL or OpenSBI!")
        print("[WARN] Simulation will likely fail without bootloader.")
        # Try without bootloader anyway
        board.set_kernel_disk_workload(
            kernel=kernel,
            disk_image=disk,
            kernel_args=["root=/dev/vda", "rw", "console=ttyS0"],
        )
else:
    print("[Error] Kernel or disk not found!")
    exit(1)

# Important: Call _pre_instantiate for AbstractBoard-based systems
# This creates the Root internally
print("[gem5] Pre-instantiating board...")
root = board._pre_instantiate(full_system=True)

print("[gem5] Instantiating...")
m5.instantiate()

print("[gem5] Starting simulation...")
exit_event = m5.simulate()

print(f"[gem5] Simulation ended at tick {m5.curTick()}")
print(f"[gem5] Cause: {exit_event.getCause()}")