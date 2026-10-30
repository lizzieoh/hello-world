# Shopping list (Amazon), 3 booths + 1 spare

Quantities cover 4 complete units. Check that each listing is the exact model before buying, since Amazon has many look-alikes.

## Order now (decided Oct 30)

4 complete units = 3 booths + 1 spare. "Have" counts what's already in hand.

| Item | Pick | Need | Have | **Order** | Why |
|---|---|---|---|---|---|
| Pi kit | **CanaKit Raspberry Pi 5 Starter Kit PRO, Turbine Black, 128GB, 4GB RAM** (Amazon, $209.99) | 4 | 1 (the 8GB PRO) | **3** | Pi 5 + 45W power supply + case with fan + heatsink + 128GB card + card reader + 2 micro-HDMI cables. All four units match the one you already have, so the spare is a true drop-in. 4GB is plenty for the booth; the 8GB becomes the studio Pi for heavier pieces later. |
| Screen | **ViewSonic ColorPro VP16-OLED**, 15.6" 1080p OLED, Pantone-validated, ~400 nits (Amazon, ~$230–250) | 4 | 0 | **4** | Best picture under 17" that's actually in stock: true blacks, accurate colour, exactly 16:9 like the videos, 3-year warranty. Backup pick: **ASUS ZenScreen OLED MQ16FC** (16" 1920×1200 OLED, ~$275; 16:10, so thin bars above and below the video). The ASUS MQ16AH and INNOCN 1080p OLED are out of stock. The booth app will move and dim its overlays so the OLED can't burn in. |
| Screen cable | **Micro-HDMI to (micro or mini)-HDMI cable**, 3 ft: match the screen's port on the listing | 4 | 0 | **6** | The Pi has micro-HDMI; listings disagree on whether the VP16's port is micro or mini (the MQ16FC is mini). One direct cable, no adapters. 2 spares. Zip-tie both ends. |
| Screen power | USB-C power supply for the screen (if not in its box) | 4 | 0 | **4** | Its own supply, so it never dims or drops when the Pi is busy. |
| Spare microSD | SanDisk, Samsung or official Raspberry Pi, 32GB+ A2 | 4 | 0 | **4** | One pre-imaged backup per Pi: a dead card is a 2-minute swap. |
| Receipt printer | **Rongta 80 mm thermal, USB, auto-cutter** | 4 | 1 | **3** | Same model as the one that works, so the code and paper are identical. (3 if you're fine sharing the spare's printer.) |
| Receipt paper | 80 mm × 80 mm thermal rolls | 1 case | sample roll | **1 case (50 rolls)** | About 1 roll per booth per few hours of heavy use. |
| Wall phone | Same model as your working one (HA9888 beige, or the pink XY-1980) | 5 | ? | **to 5 total** | 4 units + 1 practice/teardown. Same model everywhere means one keypad map. |
| Pico | [Waveshare Pico, pre-soldered headers](https://www.amazon.com/waveshare-Pre-Soldered-Raspberry-Beginner-Friendly-Microcontroller/dp/B0CNXF4PV9) | 5 | 1 | **4** | |
| USB audio adapter | [Sabrent AU-MMSA](https://www.amazon.com/dp/B00IRVQ0F8) | 5 | 1 | **4** | |
| 3.5 mm pigtails | [Fancasee TRS to bare wire, 2-pack](https://www.amazon.com/Fancasee-Replacement-Connector-Headphone-Earphone/dp/B07Y8KGJKL) | 5 packs | 1 pack | **4 packs** | |
| Spare handset cords | [Power Gear coiled cord](https://www.amazon.com/Power-Gear-Telephone-White-76122/dp/B003YKG8HW) | 4 | 0 | **4** | The most-yanked part becomes a 10-second swap. |
| USB extensions (phone → Pi) | [Cable Matters USB 2.0 6 ft, 2-pack](https://www.amazon.com/Cable-Matters-2-Pack-Extension-Extender/dp/B00N9HRUJU) | 4 packs | 0 | **4 packs** | Audio adapter + Pico per phone. |
| Small stuff | Zip ties, adhesive tie mounts, gaffer tape, label maker tape | — | — | **1 set** | Every plug strain-relieved; every cable labelled by line. |

Not needed any more (covered by the CanaKit kit): separate Pi boards, official 27W power supplies, cases, Active Coolers, micro-HDMI to HDMI cables, SD card reader.

**Alternatives considered:** CanaKit Essentials 4GB ($189.99, official Active Cooler, 32GB, 1 cable) is fine too but wouldn't match your PRO. RasTech 8GB kit: skip (third-party parts, unnamed card). Bigger screens if the screen goes on the wall instead of inside the booth: Dell P2725H 27" (matte) or Samsung QB43C 43" (commercial, 24/7). Zero-burn-in small screen: Waveshare 15.6" FHD (B), 100% sRGB IPS.

## Cables, per booth (decided Oct 30: CanaKit PRO + ARZOPA Z1FC + Rongta)

| Connection | Cable | In a box already? | Order (4 units) |
|---|---|---|---|
| Pi → screen | **Micro-HDMI to mini-HDMI**, 3–6 ft | No (kit has micro→HDMI, screen has mini→HDMI) | **6** (2 spare) |
| Screen power | **USB-C wall charger, 30W PD** + USB-C to USB-C cable | Cable usually yes; charger check the box | **4 chargers** |
| Pi power | CanaKit 45W USB-C | ✅ kit | — |
| Printer → Pi | USB-B to USB-A | ✅ printer box (plus its own power brick) | 2 spare |
| Audio adapter → Pi | plugs straight in | — | USB-A extension 3 ft only if the phone is >1 ft from the Pi |
| Pico → Pi | micro-USB to USB-A | ✅ Waveshare Pico box | 2 spare |
| Pi → network | Cat6, 10 ft | No (office test can use Wi-Fi) | in the network section |
| Wall power | Power strip, 4+ outlets | No | **1 per booth** (Pi, screen, printer) |
| Strain relief | Zip ties, adhesive tie mounts, Velcro straps | No | 1 set |

Tonight, before the micro-to-mini cable arrives: the kit's micro-HDMI→HDMI cable + the screen's mini-HDMI→HDMI cable + an **HDMI female-to-female coupler** works as a stopgap.

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
| Pi mount | Screw the CanaKit case to the inside of the enclosure (or an [ASG VESA plate](https://www.amazon.com/VESA-Mount-Raspberry-PI-Basic/dp/B079J5SFYS)) | 4 | Hides the Pi; nothing on the floor for people to kick. Leave the case fan room to breathe. |
| Screen mount | Built into the booth enclosure (the ASUS OLED has no VESA holes): a clamp bracket or a cut-out in the back panel | 4 | Angle it slightly down under booth lights to cut reflections. |
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
