# MT6878 clock correction

`0065-clk-mediatek-mt6878-preserve-independent-PLL-gates.patch` applies after
patches 0001–0064 from the `devices/titan2elite` branch, whose kernel source
profile pins MT6878-mainline/Linux commit
`12f2e9f040d0049f3f5ce5a7e53bf5ef27b46422`. That branch currently contains the
remaining Titan kernel profile and build inputs.

The patch assigns each set/clear PLL its declared enable bit and the correct
register operations. In the previous descriptors, all these clocks defaulted
to bit zero (APLL2), allowing another PLL's shutdown to stop active audio.
The helper preserves audio tuner handling and does not change other providers'
operation selection.

On the development Titan, direct ALSA music cuts out while the PCM keeps running;
APLL2's enable bit is off during the gaps. An internal DL0→UL0 test matched 5,000
sampled generated audio blocks at constant delay. A corrected kernel has not yet
been installed or audibly qualified. This patch is a source candidate, not a
published kernel or OS update.
