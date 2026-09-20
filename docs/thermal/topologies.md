# Thermal Topologies v0.1

INS-EI initially supports three standard thermal topologies.

## DIRECT

No buffer. A heat generator supplies one or more heating circuits directly. A separate DHW storage may exist.

## BUFFER

A buffer is the central thermal hub. Heat sources charge the buffer; heating circuits and optional DHW charging draw from it.

## COMBINED_STORAGE

Space-heating buffer and domestic-hot-water function share one thermally coupled storage system. It must not be modeled as two independent energy stores because that would double-count stored heat.

Special hydraulic solutions are represented by explicit connections/actions on top of these standard topologies rather than customer-specific optimizer code.
