# Myntra VMS — `python run.py vms`

**When**: 5:30 PM daily. On demand: `/vms`, `/vms FROM TO`, `/vms erp`, `/vmspic [YYYY-MM]`.

**Result**: `VMS/MYNTRA ORDER ID WISE VMS.xlsx` (`VMS_DRIVE_FILE_ID`).

* Per SLA date and Myntra channel (`MYNTRA_CHANNELS`): ORDER COUNT = orders with an AWB, VMS COUNT =
  those whose Myntra AWB has a packing scan in VMS (`fkvms.synlabs.io`). For Shadowfax / Delhivery
  orders OMSGuru shows the courier AWB; the Myntra number is decoded from the shipping label barcode.
* **ERP vs VMS**: parcels packed in the ERP (report 62, portal MYNTRA: its Order ID is the Myntra
  tracking number) yesterday and today, with / without a VMS scan, per channel: sheets
  `ERP VS VMS MON-YY` and `ERP NO VMS` (who packed it). Only those two days are replaced. If the ERP
  part fails the VMS report still goes out with a note.
