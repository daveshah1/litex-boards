#!/usr/bin/env python3

import argparse

from migen import *

from litex_boards.community.platforms import zcu104

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import mem_decoder
from litex.soc.integration.soc_sdram import *
from litex.soc.integration.builder import *

from litedram.modules import MT40A512M8
from litedram.phy import usddrphy

# CRG ----------------------------------------------------------------------------------------------

class _CRG(Module):
    def __init__(self, platform, sys_clk_freq, with_sdram):
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_sys4x = ClockDomain(reset_less=True)
        self.clock_domains.cd_clk200 = ClockDomain()
        self.clock_domains.cd_ic = ClockDomain()

        # # #

        self.cd_sys.clk.attr.add("keep")
        self.cd_sys4x.clk.attr.add("keep")

        self.submodules.pll = pll = USMMCM(speedgrade=-2)
        cpu_reset = platform.request("cpu_reset")
        self.comb += pll.reset.eq(cpu_reset)
        self.comb += platform.request("user_led", 2).eq(pll.locked)
        self.clock_domains.cd_pll4x = ClockDomain(reset_less=True)
        pll.register_clkin(platform.request("clk125"), 125e6)
        pll.create_clkout(self.cd_pll4x, sys_clk_freq*4, buf=None, with_reset=False)
        pll.create_clkout(self.cd_clk200, 200e6, with_reset=False)

        self.specials += [
            Instance("BUFGCE_DIV", name="main_bufgce_div",
                p_BUFGCE_DIVIDE=4,
                i_CE=1, i_I=self.cd_pll4x.clk, o_O=self.cd_sys.clk),
            Instance("BUFGCE", name="main_bufgce",
                i_CE=1, i_I=self.cd_pll4x.clk, o_O=self.cd_sys4x.clk),
            AsyncResetSynchronizer(self.cd_clk200, ~pll.locked | cpu_reset),
        ]

        ic_reset_counter = Signal(max=64, reset=63)
        ic_reset = Signal(reset=1)
        self.sync.clk200 += \
            If(ic_reset_counter != 0,
                ic_reset_counter.eq(ic_reset_counter - 1)
            ).Else(
                ic_reset.eq(0)
            )
        ic_rdy = Signal()
        ic_rdy_counter = Signal(max=64, reset=63)
        self.cd_sys.rst.reset = 1
        self.comb += self.cd_ic.clk.eq(self.cd_sys.clk)
        self.sync.ic += [
            If(ic_rdy,
                If(ic_rdy_counter != 0,
                    ic_rdy_counter.eq(ic_rdy_counter - 1)
                ).Else(
                    self.cd_sys.rst.eq(0)
                )
            )
        ]

        if with_sdram:
            self.specials += [
                Instance("IDELAYCTRL", p_SIM_DEVICE="ULTRASCALE",
                         i_REFCLK=ClockSignal("clk200"), i_RST=ic_reset,
                         o_RDY=ic_rdy),
                AsyncResetSynchronizer(self.cd_ic, ic_reset)
            ]
        else:
            self.comb += [
                ic_rdy.eq(1)
            ]
            self.specials += [
                AsyncResetSynchronizer(self.cd_ic, ic_reset)
            ]

        # Prevent ARM cores from crashing after loading the bitstream
        self.specials += [
            Instance("PS8", attr=["keep"]),
        ]
# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCSDRAM):
    def __init__(self, sys_clk_freq=int(125e6), **kwargs):
        platform = zcu104.Platform()
        SoCSDRAM.__init__(self, platform, clk_freq=sys_clk_freq,
                         integrated_rom_size=0x8000,
                         integrated_sram_size=0x8000,
                          **kwargs)

        self.submodules.crg = _CRG(platform, sys_clk_freq, not self.integrated_main_ram_size)

        led0 = platform.request("user_led", 0)
        led1 = platform.request("user_led", 1)

        led_counter_n = Signal(32)
        self.sync += led_counter_n.eq(led_counter_n + 1)
        self.comb += led0.eq(led_counter_n[25])

        led_counter = Signal(32)
        led_counter.reset = 0
        self.sync += led_counter.eq(led_counter + 1)
        self.comb += led1.eq(led_counter[26])

        # sdram
        if not self.integrated_main_ram_size:
            self.submodules.ddrphy = usddrphy.USDDRPHY(platform.request("ddram"), memtype="DDR4", device="ULTRASCALE_PLUS", sys_clk_freq=sys_clk_freq, cmd_latency=1)
            self.add_csr("ddrphy")
            self.add_constant("USDDRPHY", None)
            sdram_module = MT40A512M8(sys_clk_freq, "1:4")
            self.register_sdram(self.ddrphy,
                                sdram_module.geom_settings,
                                sdram_module.timing_settings)


# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteX SoC on ZCU104")
    builder_args(parser)
    soc_sdram_args(parser)
    parser.add_argument("--with-sdram", action="store_true",
                help="enable DDR4 SODIMM support")
    parser.add_argument("--with-ethernet", action="store_true",
                    help="enable Ethernet support")
    args = parser.parse_args()

    cls = BaseSoC
    soc = cls(integrated_main_ram_size=0x8000 if not args.with_sdram else None, **soc_sdram_argdict(args))
    builder = Builder(soc, **builder_argdict(args))
    builder.build()


if __name__ == "__main__":
    main()
