#!/usr/bin/env python3
"""
gem5 script to run a RISC-V qcow2 disk image saved from QEMU.
Automatically converts qcow2 to raw format since gem5 only supports raw images.

Usage:
    ./gem5.opt run_riscv_qcow2.py <disk.qcow2> [--kernel=vmlinux] [--cpu=timing] [--mem=4GB]
"""

import os
import sys
import subprocess
from pathlib import Path

# gem5 imports
from m5.objects import (
    AddrRange,
    BadAddr,
    Bridge,
    CowDiskImage,
    HiFive,
    IOXBar,
    MemCtrl,
    DDR4_2400_8x8,
    PMAChecker,
    RawDiskImage,
    RiscvBootloaderKernelWorkload,
    RiscvMmioVirtIO,
    RiscvRTC,
    RiscvSystem,
    Root,
    SystemXBar,
    VirtIOBlock,
    RiscvAtomicSimpleCPU,
    RiscvTimingSimpleCPU,
    RiscvO3CPU,
    RiscvMinorCPU,
    Frequency,
    SrcClockDomain,
    VoltageDomain,
)
from m5.util.fdthelper import (
    Fdt,
    FdtNode,
    FdtProperty,
    FdtPropertyStrings,
    FdtPropertyWords,
    FdtState,
)
import m5


# =============================================================================
# Configuration (modify these or use gem5's --param syntax)
# =============================================================================

# Default paths - modify these for your setup
DISK_IMAGE = '/home/luming/disk.qcow2'
KERNEL_PATH = '/home/luming/linux/vmlinux'
BOOTLOADER_PATH = None  # e.g., '/home/luming/bbl.bin' if needed

# CPU configuration
CPU_TYPE = 'timing'  # 'atomic', 'timing', 'o3', 'minor'
NUM_CPUS = 1

# Memory configuration
MEM_SIZE = '4GB'

# Boot arguments
BOOT_ARGS = 'root=/dev/vda rw console=ttyS0 earlycon=sbi init=/sbin/init'

# Cache directory for converted images
CACHE_DIR = '/home/luming/gem5-cache'

# Maximum simulation ticks (None = run until exit)
MAX_TICKS = None  # e.g., 100000000000 for 100 billion ticks


# =============================================================================
# Helper Functions
# =============================================================================

def qcow2_to_raw(qcow2_path: str, raw_path: str) -> bool:
    """Convert qcow2 image to raw format using qemu-img."""
    try:
        print(f"[Convert] {qcow2_path} -> {raw_path}")
        subprocess.run(
            ['qemu-img', 'convert', '-f', 'qcow2', '-O', 'raw',
             qcow2_path, raw_path],
            capture_output=True, text=True, check=True
        )
        size_mb = os.path.getsize(raw_path) // (1024 * 1024)
        print(f"[Convert] Success! Size: {size_mb} MB")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[Error] Conversion failed: {e.stderr}")
        return False
    except FileNotFoundError:
        print("[Error] qemu-img not found. Install: sudo apt install qemu-utils")
        return False


def get_raw_image(qcow2_path: str, cache_dir: str = None) -> str:
    """Get raw disk image path, converting from qcow2 if necessary."""
    qcow2_path = os.path.abspath(qcow2_path)

    if not os.path.exists(qcow2_path):
        raise FileNotFoundError(f"Image not found: {qcow2_path}")

    # Determine raw image path
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        raw_path = os.path.join(cache_dir, Path(qcow2_path).stem + '.raw')
    else:
        raw_path = qcow2_path.rsplit('.', 1)[0] + '.raw'

    # Check cache
    if os.path.exists(raw_path):
        if os.path.getmtime(raw_path) >= os.path.getmtime(qcow2_path):
            print(f"[Cache] Using cached raw image: {raw_path}")
            return raw_path

    if not qcow2_to_raw(qcow2_path, raw_path):
        raise RuntimeError("Failed to convert qcow2 to raw")

    return raw_path


def parse_mem_size(mem_str):
    """Parse memory size string to bytes."""
    mem_str = mem_str.upper().strip()
    units = {
        'KB': 1024, 'K': 1024,
        'MB': 1024 * 1024, 'M': 1024 * 1024,
        'GB': 1024 * 1024 * 1024, 'G': 1024 * 1024 * 1024,
    }
    for unit, multiplier in units.items():
        if mem_str.endswith(unit):
            return int(mem_str[:-len(unit)]) * multiplier
    return int(mem_str)


# =============================================================================
# RISC-V System Class
# =============================================================================

class RiscvLinuxSystem(RiscvSystem):
    """RISC-V Linux full system configuration for gem5."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Voltage domain for the system
        self.voltage_domain = VoltageDomain(voltage='1V')

        # Clock domain for the system
        self.clk_domain = SrcClockDomain(clock=Frequency('1GHz'),
                                          voltage_domain=self.voltage_domain)

        # Memory setup - RISC-V memory starts at 0x80000000
        mem_size_bytes = parse_mem_size(MEM_SIZE)
        self.mem_ranges = [AddrRange(start=0x80000000, size=mem_size_bytes)]

        # Memory mode based on CPU type
        self.mem_mode = 'timing' if CPU_TYPE != 'atomic' else 'atomic'

        # Create CPUs
        self.cpu = [self._create_cpu(CPU_TYPE, i) for i in range(NUM_CPUS)]

        # Set up workload
        self.workload = RiscvBootloaderKernelWorkload()

        # Platform with CLINT, PLIC, UART
        self._setup_platform(NUM_CPUS)

        # Create I/O bus first (needed for CPU port connection)
        self._setup_io_bus()

        # Memory system
        self._setup_memory()

        # I/O devices
        self._setup_io_devices()

        # Disk image
        if DISK_IMAGE:
            raw_image = get_raw_image(DISK_IMAGE, CACHE_DIR)
            self._setup_disk(raw_image)

        # Kernel configuration
        if KERNEL_PATH:
            self.workload.object_file = KERNEL_PATH
            self.workload.kernel_addr = 0x80000000

        # Bootloader configuration
        if BOOTLOADER_PATH:
            self.workload.bootloader_filename = BOOTLOADER_PATH
            self.workload.bootloader_addr = 0x80000000
            self.workload.kernel_addr = 0x80200000
            self.workload.entry_point = 0x80000000
        else:
            self.workload.entry_point = 0x80000000

        # Boot arguments
        self.workload.command_line = BOOT_ARGS

        # DTB address
        self.workload.dtb_addr = 0x87E00000

    def _create_cpu(self, cpu_type, cpu_id):
        """Create a RISC-V CPU of specified type."""
        cpu_classes = {
            'atomic': RiscvAtomicSimpleCPU,
            'timing': RiscvTimingSimpleCPU,
            'o3': RiscvO3CPU,
            'minor': RiscvMinorCPU,
        }
        cpu_type_lower = cpu_type.lower()
        if cpu_type_lower not in cpu_classes:
            print(f"[Warn] Unknown CPU type '{cpu_type}', using timing")
            cpu_type_lower = 'timing'

        cpu = cpu_classes[cpu_type_lower](cpu_id=cpu_id)
        cpu.createThreads()
        cpu.createInterruptController()
        return cpu

    def _setup_platform(self, num_cpus):
        """Set up HiFive platform with CLINT, PLIC, UART."""
        self.platform = HiFive()
        self.platform.plic.hart_config = ",".join(["MS" for _ in range(num_cpus)])
        self.platform.attachPlic()
        self.platform.clint.num_threads = num_cpus
        self.platform.rtc = RiscvRTC(frequency=Frequency("100MHz"))
        self.platform.clint.int_pin = self.platform.rtc.int_pin

        # Set up PCI bus for config_error connection
        self.platform.pci_bus.config_error_port = self.platform.pci_host.config_error.pio

    def _setup_memory(self):
        """Set up memory system with bus and controller."""
        self.membus = SystemXBar()
        self.membus.badaddr_responder = BadAddr()
        self.membus.default = self.membus.badaddr_responder.pio
        self.system_port = self.membus.cpu_side_ports

        self.mem_ctrl = MemCtrl()
        self.mem_ctrl.dram = DDR4_2400_8x8()
        self.mem_ctrl.dram.range = self.mem_ranges[0]
        self.mem_ctrl.port = self.membus.mem_side_ports

        # Connect CPUs to memory bus directly
        for cpu in self.cpu:
            # Connect instruction and data cache ports
            cpu.icache_port = self.membus.cpu_side_ports
            cpu.dcache_port = self.membus.cpu_side_ports

            # Connect writeback port if it exists
            if hasattr(cpu, 'wb_port'):
                cpu.wb_port = self.membus.cpu_side_ports

    def _setup_io_bus(self):
        """Set up I/O bus (needed before CPU connection)."""
        self.iobus = IOXBar()
        self.iobus.badaddr_responder = BadAddr()
        self.iobus.default = self.iobus.badaddr_responder.pio

    def _setup_io_devices(self):
        """Set up I/O bridge and devices."""
        self.bridge = Bridge(delay="10ns")
        self.bridge.mem_side_port = self.iobus.cpu_side_ports
        self.bridge.cpu_side_port = self.membus.mem_side_ports

        self.bridge.ranges = [
            AddrRange(0x10000000, size=0x1000),   # UART
            AddrRange(0x10001000, size=0x1000),
            AddrRange(0x10007000, size=0x1000),
            AddrRange(0x10008000, size=0x1000),   # VirtIO
            AddrRange(0x2F000000, size=0x1000000),  # PCI config
            AddrRange(0x30000000, size=0x10000000), # PCI memory
            AddrRange(0x40000000, size=0x20000000), # PCI AXI
        ]

        # Connect PCI host to I/O bus
        self.iobus.mem_side_ports = self.platform.pci_host.up_response_port()
        self.iobus.cpu_side_ports = self.platform.pci_host.up_request_port()
        self.platform.pci_bus.default = self.platform.pci_host.down_response_port()
        self.platform.pci_bus.cpu_side_ports = self.platform.pci_host.down_request_port()

        self.platform.uart.pio = self.iobus.mem_side_ports
        self.platform.clint.pio = self.membus.mem_side_ports
        self.platform.plic.pio = self.membus.mem_side_ports

        uncacheable_ranges = [
            AddrRange(0x10000000, size=0x20000000),  # I/O region
            AddrRange(0x2F000000, size=0x1000000),   # PCI config
            AddrRange(0x30000000, size=0x10000000),  # PCI memory
        ]
        for cpu in self.cpu:
            cpu.mmu.pma_checker = PMAChecker(uncacheable=uncacheable_ranges)

    def _setup_disk(self, disk_image):
        """Set up VirtIO disk with the provided image."""
        image = CowDiskImage(read_only=False)
        image.child = RawDiskImage(read_only=True)
        image.child.image_file = disk_image

        self.disk = RiscvMmioVirtIO(
            vio=VirtIOBlock(),
            interrupt_id=0x8,
            pio_size=4096,
            pio_addr=0x10008000,
        )
        self.disk.vio.image = image
        self.disk.pio = self.iobus.mem_side_ports

    def generate_device_tree(self, outdir):
        """Generate device tree for the system."""
        state = FdtState(addr_cells=2, size_cells=2, cpu_cells=1)
        root = FdtNode("/")
        root.append(state.addrCellsProperty())
        root.append(state.sizeCellsProperty())
        root.appendCompatible(["riscv-virtio"])

        # Memory node
        for mem_range in self.mem_ranges:
            node = FdtNode(f"memory@{int(mem_range.start):x}")
            node.append(FdtPropertyStrings("device_type", ["memory"]))
            node.append(FdtPropertyWords("reg",
                state.addrCells(mem_range.start) + state.sizeCells(mem_range.size())))
            root.append(node)

        # Chosen node
        chosen = FdtNode("chosen")
        chosen.append(FdtPropertyStrings("bootargs", [self.workload.command_line]))
        chosen.append(FdtPropertyStrings("stdout-path", ["/soc/uart@10000000"]))
        root.append(chosen)

        # CPUs node
        cpus_node = FdtNode("cpus")
        cpus_state = FdtState(addr_cells=1, size_cells=0)
        cpus_node.append(cpus_state.addrCellsProperty())
        cpus_node.append(cpus_state.sizeCellsProperty())
        cpus_node.append(FdtPropertyWords("timebase-frequency", [100000000]))

        for i, cpu in enumerate(self.cpu):
            node = FdtNode(f"cpu@{i}")
            node.append(FdtPropertyStrings("device_type", "cpu"))
            node.append(FdtPropertyWords("reg", state.CPUAddrCells(i)))
            node.append(FdtPropertyStrings("mmu-type", "riscv,sv48"))
            node.append(FdtPropertyStrings("status", "okay"))
            node.append(FdtPropertyStrings("riscv,isa", "rv64gc"))
            node.append(FdtPropertyWords("clock-frequency", [1000000000]))
            node.appendCompatible(["riscv"])
            node.appendPhandle(f"cpu@{i}")
            cpus_node.append(node)
        root.append(cpus_node)

        # SOC node
        soc_node = FdtNode("soc")
        soc_state = FdtState(addr_cells=2, size_cells=2)
        soc_node.append(soc_state.addrCellsProperty())
        soc_node.append(soc_state.sizeCellsProperty())
        soc_node.append(FdtProperty("ranges"))
        soc_node.appendCompatible(["simple-bus"])

        # CLINT
        clint = self.platform.clint
        clint_node = clint.generateBasicPioDeviceNode(
            soc_state, "clint", clint.pio_addr, clint.pio_size)
        clint_node.appendCompatible(["riscv,clint0"])
        soc_node.append(clint_node)

        # PLIC
        plic = self.platform.plic
        plic_node = plic.generateBasicPioDeviceNode(
            soc_state, "plic", plic.pio_addr, plic.pio_size)
        plic_node.append(FdtProperty("interrupt-controller"))
        plic_node.appendCompatible(["riscv,plic0"])
        plic_node.append(FdtPropertyWords("riscv,ndev", [plic.n_src - 1]))
        soc_node.append(plic_node)

        # UART
        uart = self.platform.uart
        uart_node = uart.generateBasicPioDeviceNode(
            soc_state, "uart", uart.pio_addr, uart.pio_size)
        uart_node.append(FdtPropertyWords("interrupts", [0x1]))
        uart_node.append(FdtPropertyWords("clock-frequency", [0x384000]))
        uart_node.appendCompatible(["ns8250", "ns16550a"])
        soc_node.append(uart_node)

        # VirtIO disk
        if hasattr(self, 'disk'):
            disk = self.disk
            disk_node = disk.generateBasicPioDeviceNode(
                soc_state, "virtio_mmio", disk.pio_addr, disk.pio_size)
            disk_node.append(FdtPropertyWords("interrupts", [disk.interrupt_id]))
            disk_node.appendCompatible(["virtio,mmio"])
            soc_node.append(disk_node)

        root.append(soc_node)

        # Write device tree
        fdt = Fdt()
        fdt.add_rootnode(root)
        fdt.writeDtsFile(os.path.join(outdir, "device.dts"))
        fdt.writeDtbFile(os.path.join(outdir, "device.dtb"))
        self.workload.dtb_filename = os.path.join(outdir, "device.dtb")


# =============================================================================
# Main Execution (gem5-style, no argparse)
# =============================================================================

print("[Setup] Creating RISC-V system...")
print(f"  CPU: {CPU_TYPE} x {NUM_CPUS}")
print(f"  Memory: {MEM_SIZE}")
print(f"  Disk: {DISK_IMAGE}")
print(f"  Kernel: {KERNEL_PATH}")
if BOOTLOADER_PATH:
    print(f"  Bootloader: {BOOTLOADER_PATH}")

# Create the system
system = RiscvLinuxSystem()

# Generate device tree
outdir = m5.options.outdir
os.makedirs(outdir, exist_ok=True)
system.generate_device_tree(outdir)
print(f"[Setup] Device tree generated in: {outdir}")

# Create root and instantiate
root = Root(full_system=True, system=system)
print("[gem5] Instantiating...")
m5.instantiate()

# Run simulation
print("[gem5] Starting simulation...")
if MAX_TICKS:
    exit_event = m5.simulate(MAX_TICKS)
else:
    exit_event = m5.simulate()

print(f"\n[gem5] Simulation ended at tick {m5.curTick()}")
print(f"[gem5] Cause: {exit_event.getCause()}")
print(f"[gem5] Exit code: {exit_event.getCode()}")