# Shopping list (Amazon), 3 booths + 1 spare

Quantities cover 4 complete units. Check that each listing is the exact model before buying, since Amazon has many look-alikes.

## Prototype: one complete booth (buy this first)

Everything for one working line, end to end. Already in hand from the build so far: the wall phone, Pico, Sabrent adapter, 3.5 mm pigtails, soldering kit + multimeter, Rongta 80 mm printer.

| Item | Pick | Qty | Why this one won't fail |
|---|---|---|---|
| Computer | **Raspberry Pi 5, 4GB** from an official reseller (PiShop.us, Adafruit, CanaKit) | 1 | The booth app uses well under 1 GB. 8GB/16GB cost much more since the 2026 RAM price rises and add nothing here. Avoid the Pi 4 (slower) and the Pi 5 2GB (no headroom for the browser fallback). |
| Cooling | **Official Raspberry Pi Active Cooler** | 1 | Heatsink + fan on the chip. `setup-native.sh` sets a stronger fan curve for it, and the app logs the temperature. Behind a monitor in a warm booth, passive cooling throttles. |
| Power | **Official Raspberry Pi 27W USB-C PSU** | 1 | The #1 cause of random Pi reboots is a weak supply. |
| Storage | **Official Raspberry Pi microSD card, 64GB (A2)** | 2 | Second one is a pre-imaged spare: a dead card becomes a 2-minute swap. The read-only filesystem (manual Part 15) means power cuts can't corrupt it. |
| Monitor | **Dell P2725H, 27" 1080p IPS** ([Staples](https://www.staples.com/dell-27-fhd-100hz-monitor-matte-black-silver-p2725h/product_24601206), [CDW](https://www.cdw.com/product/Dell-27-Monitor-P2725H/7878801)) | 1 | Matte anti-glare (booth lights), VESA 100×100, HDMI + DisplayPort, Dell's business line. 1080p is plenty: the videos are 768p/720p. |
| Video cable | **Official Raspberry Pi micro-HDMI to HDMI cable** | 1 (+1 spare) | The Pi's micro-HDMI port is the most fragile plug in the booth: zip-tie the cable to the mount so nobody can lever it. |
| Receipt paper | 80 mm × 80 mm thermal rolls | 1 box | The printer ships with a sample roll only. |
| Setup | Any USB keyboard + Ethernet cable | 1 | Only for setup; after that it's SSH. |

For show conditions at home: add the **GL.iNet travel router** and a phone tethered by USB as the backup, then unplug the Wi-Fi mid-call to watch it fail over.

## Per unit (×4)

| Item | Pick | Qty | Why |
|---|---|---|---|
| Landline phone | [Cortelco ITT-2500 single-line desk phone](https://www.amazon.com/Cortelco-ITT-2500-MD-BK-Single-Line-Telephone/dp/B002LLH3Q0) | **5** | The real 2500 design: heavy, simple and repairable, with a standard modular handset. The 5th is a teardown/practice unit. |
| Phone, budget / color alt | [HTMengt 2500-style retro phone](https://www.amazon.com/Desk-Telephone-2500-Analog-Phone/dp/B0BKZHJNMC) · [red](https://www.amazon.com/Desk-Telephone-2500-Analog-Phone/dp/B0BKZDZ63C) | (5) | Lighter, cheaper, more colors. Less sturdy. |
| Phone, wall-mount option | [Sangyn retro wall phone, beige](https://www.amazon.com/Mechanical-Sangyn-Adjustable-Fashioned-Telephone/dp/B0BRC8TM4W) · [red](https://www.amazon.com/Landline-Telehone-Waterproof-Moisture-Bathroom/dp/B07RBMP5V4) | (5) | Screws to the booth wall, so it can't be knocked over or carried off, and cables hide behind the wall. Monitor mounts directly above. Buy 1 first and open it to check the keypad and hook switch. |
| Raspberry Pi 5 (4GB) | [Raspberry Pi 5 4GB](https://www.amazon.com/Raspberry-4GB-Single-Board-Computer/dp/B0CPM3SM72) | 4 | Amazon Pi listings are third-party. PiShop.us, Adafruit and CanaKit are official resellers and often cheaper. |
| Official 27W PSU | [Raspberry Pi 27W USB-C Power Supply](https://www.amazon.com/Raspberry-Pi-USB-C-Power-Supply/dp/B0D3MFLNC1) | 5 | Official only. Cheap PSUs cause random reboots. |
| Cooling | Official Raspberry Pi Active Cooler | 4 | The fan curve in `setup-native.sh` is tuned for it. Mount the Pi on the open VESA plate, not in a closed box. |
| microSD | Official Raspberry Pi microSD 64GB (A2), or [SanDisk Extreme 64GB A2](https://www.amazon.com/SanDisk-Extreme-microSDXC-Memory-Adapter/dp/B09X7C7LL1) | 8 | 2 per unit; the second is a pre-imaged spare. |
| Pico (hook + keypad) | [Waveshare Pico, pre-soldered headers + USB cable](https://www.amazon.com/waveshare-Pre-Soldered-Raspberry-Beginner-Friendly-Microcontroller/dp/B0CNXF4PV9) | 5 | Pre-soldered headers let you use plug-on jumper wires. |
| USB audio adapter | [Sabrent AU-MMSA](https://www.amazon.com/dp/B00IRVQ0F8) | 5 | Separate mic and headphone jacks, driverless on Linux. |
| 3.5mm pigtails | [Fancasee 3.5mm TRS to bare wire, 2-pack](https://www.amazon.com/Fancasee-Replacement-Connector-Headphone-Earphone/dp/B07Y8KGJKL) | 5 packs | |
| Micro-HDMI → HDMI | Official Raspberry Pi micro-HDMI to HDMI cable, or [Cable Matters 6ft, 2-pack](https://www.amazon.com/Cable-Matters-2-Pack-Speed-Resolution/dp/B00B9H6VT2) | 6 | One per unit + spares. Zip-tie it to the mount: the Pi's micro-HDMI port is the most fragile plug in the booth. |
| Monitor | **Dell P2725H 27" 1080p IPS, matte** ([Staples](https://www.staples.com/dell-27-fhd-100hz-monitor-matte-black-silver-p2725h/product_24601206)) | **4** | Matte, VESA 100×100, HDMI + DP. The 4th makes the spare unit complete: a dead monitor is a 5-minute swap, not a dark booth. (Replaces the glossy S2721NX.) |
| Spare handset cords | [Power Gear coiled handset cord](https://www.amazon.com/Power-Gear-Telephone-White-76122/dp/B003YKG8HW) or [NAC 25ft black](https://www.amazon.com/Telephone-Handset-Coiled-COnducter-Hand-Set/dp/B00ULSEDHM) | 4 | 4P4C ends. The booth's most-yanked part becomes a 10-second swap. |
| Electret mic capsules | [uxcell 9.7mm electret, 10-pack](https://www.amazon.com/uxcell-Capsule-Electret-Condenser-Microphone/dp/B00OK65LKY) | 1 pack | Only needed if the handset turns out to have an old carbon mic. Cheap insurance. |

## Shared network (×1)

| Item | Pick | Qty | Why |
|---|---|---|---|
| Travel router | [GL.iNet GL-MT3000 Beryl AX](https://www.amazon.com/GL-iNet-GL-MT3000-Pocket-Sized-Wireless-Gigabit/dp/B0BPSGJN7T) | 2 | Second one is a spare. The [Beryl 7 (GL-MT3600BE)](https://www.gl-inet.com/blogs/blog/glinet-launches-beryl-7-travel-router) works equally well. Buy 2 of the same model so the spare takes a saved config backup. |
| Switch | [TP-Link TL-SG108, 8-port gigabit](https://www.amazon.com/TP-Link-Gigabit-Ethernet-Network-Switch/dp/B00A121WN6) | 1 | |
| Ethernet | [Cable Matters Cat6 10ft, 5-pack](https://www.amazon.com/Cable-Matters-5-Pack-Snagless-Ethernet/dp/B01CRFVGYO) | 2 packs | Cat6 is plenty, and more flexible than Cat8. |
| Backup internet | [Netgear Nighthawk M6 5G hotspot (MR6150)](https://www.amazon.com/NETGEAR-Nighthawk-Mobile-Hotspot-MR6150/dp/B0BGV79FHT) + a data SIM | 1 | Optional: a phone USB-tethered to the router also works. Use whichever carrier is strongest at the venue. |

## Mounting, cabling & power

| Item | Pick | Qty | Why |
|---|---|---|---|
| Pi on back of monitor | [ASG VESA mount for Pi 5](https://www.amazon.com/VESA-Mount-Raspberry-PI-Basic/dp/B079J5SFYS) | 4 | Hides the Pi; nothing on the floor for people to kick. |
| Monitor wall mount | [Fixed VESA slim wall mount](https://www.amazon.com/Fixed-VESA-Slim-Wall-Mount/dp/B011LODZJG) | 3 | Check the monitor's VESA pattern (100×100 for the Dell S2721NX). |
| USB extensions (phone → Pi) | [Cable Matters USB 2.0 extension 6ft, 2-pack](https://www.amazon.com/Cable-Matters-2-Pack-Extension-Extender/dp/B00N9HRUJU) | 5 packs | 2 per phone, for the audio adapter and the Pico. |
| Battery backup | [APC BE600M1 600VA UPS](https://www.amazon.com/APC-respaldo-protector-sobretensiones-alimentaci%C3%B3n/dp/B01FWAZEIU) | 2 | One for the router/switch/hotspot, one for the Pis. Venue power blips won't reboot anything. |
| Power strip | [Tripp Lite SS3612, 12-outlet metal, 15ft](https://www.amazon.com/Tripp-Lite-Outlet-Protector-SS3612/dp/B00UK4BP1O) | 2 | For the monitors. Plug the UPS units straight into the wall, not into a strip. |
| SD card reader | [SanDisk MobileMate USB 3.0](https://www.amazon.com/SanDisk-MobileMate-microSD-Card-Reader/dp/B07G5JV2B5) | 1 | For flashing and cloning cards from a laptop. |

## Not on Amazon, but don't forget

- [ ] **Booth power order** through Adobe MAX exhibitor services, which usually has an early-order deadline. You need roughly 3 monitors + 3 Pis + network gear.
- [ ] **Exhibitor manual: Wi-Fi rules.** Many venues ban exhibitor Wi-Fi access points and hotspots. Ask in writing. On site, turn off the GL.iNet's own broadcast network (repeater mode still works).
- [ ] **Wired internet drop** quote from the venue: the single biggest reliability upgrade.
- [ ] **Data SIM** for the hotspot, on whichever carrier is strongest at the venue.
- [ ] **fal account ready for the crowd:** enough credits, concurrency/rate limits that cover 3 booths all day, and the API key on a server, not in the browser.
- [ ] **Content safety:** the public will say anything. Turn on the image model's safety checker and add a prompt filter. Your brand is on the screen.
- [ ] **Privacy:** a small sign ("Your voice is used to make your image and isn't stored"). Make sure that statement is actually true in the app.
- [ ] **Instruction sign at each phone:** "Pick up · Say what you want to see · Press 2 to hear it · Press 3 to try again."
- [ ] **Attract loop:** what the screen shows when nobody is on the phone.
- [ ] **Booth wall/panel** solid enough to screw the phones and monitors into, with holes for cables.
- [ ] **Padded cases** for monitors and kits, plus the go-bag from the guide.

## Tools & supplies

| Item | Pick |
|---|---|
| Soldering station + multimeter | [Plusivo 60W station kit with multimeter](https://www.amazon.com/Adjustable-Temperature-Calibration-Electricians-Plusivo/dp/B0B3JW8BVW) |
| Also | female-to-female jumper wires, heat-shrink assortment, hot glue gun, zip ties + adhesive mounts, gaffer tape, label maker, a USB keyboard, isopropyl wipes, foam mic covers |
