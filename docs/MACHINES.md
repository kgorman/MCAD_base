# Getting programs to machines: two worked examples

A machine runs a file from a released revision, never from `wip/`. How the
file gets there depends on what the control can do. This document walks
through two common cases: a Tormach running PathPilot, which can only serve
files, and a Haas with the Next Generation Control, which can mount a share.
The layout rules are in [CANONICAL_TREE.md](CANONICAL_TREE.md#getting-a-release-to-a-machine).

## The release both machines run

Posting from CAM gives one program per machine model. They go into the
revision under `cam/<model>/`, lowercase, no spaces, and the manifest lists
both models as proven on. Each program has its setup sheet beside it.

```
Kenny's Hub/Bike/Motor Mount/released/rev-a/
  manifest.json                        "process": { "proven_on": ["tormach-pcnc440", "haas-vf2"] }
  SHA256SUMS
  cad/motor-mount.f3d
  drawings/motor-mount_rev-a.pdf
  cam/tormach-pcnc440/
    o0042_op10_rev-a.nc                PathPilot post
    o0042_op10_rev-a_setup-sheet.pdf
  cam/haas-vf2/
    o0042_op10_rev-a.nc                Haas NGC post; the file starts with %, O00042
    o0042_op10_rev-a_setup-sheet.pdf
```

Two things about the names. The revision is in the file name, because the
name is all a control shows before the program is loaded. And the setup
sheet is named after its program, so the two sort together and the name is
still unique once the file is copied out of its folder.

Deep paths and names with spaces are fine for people and for the tools,
but not for every control. `add-machine` makes a flat folder per machine
under `_outbox/`, and at each release the current programs are copied into
it, replacing the old ones. Nothing is edited there; the copy in the
revision stays frozen and checksummed.

The operator needs more than the program: the setup sheet, and the drawing
for checks at the machine. Those go into a second flat folder,
`_outbox/<machine>-docs/`, read from a tablet or a PC at the machine. It
is kept apart from the programs so that the control's list shows programs
and nothing else.

```sh
./mcad_tree.py add-machine /path/to/store tormach-pcnc440
./mcad_tree.py add-machine /path/to/store tormach-pcnc440-docs
./mcad_tree.py add-machine /path/to/store haas-vf2
./mcad_tree.py add-machine /path/to/store haas-vf2-docs

cd "/path/to/store/Kenny's Hub/Bike/Motor Mount/released/rev-a"
cp cam/tormach-pcnc440/*.nc  /path/to/store/_outbox/tormach-pcnc440/
cp cam/tormach-pcnc440/*.pdf drawings/*.pdf /path/to/store/_outbox/tormach-pcnc440-docs/
cp cam/haas-vf2/*.nc         /path/to/store/_outbox/haas-vf2/
cp cam/haas-vf2/*.pdf        drawings/*.pdf /path/to/store/_outbox/haas-vf2-docs/
```

When a revision replaces an older one, delete the older revision's files
from both folders, so the outbox holds only what is current.

```
<root>/
  _outbox/
    tormach-pcnc440/
      o0042_op10_rev-a.nc                <- the store pushes this to the Tormach
    tormach-pcnc440-docs/
      o0042_op10_rev-a_setup-sheet.pdf   <- read on a tablet or PC at the machine
      motor-mount_rev-a.pdf
    haas-vf2/
      o0042_op10_rev-a.nc                <- the Haas reads this over the network
    haas-vf2-docs/
      o0042_op10_rev-a_setup-sheet.pdf
      motor-mount_rev-a.pdf
```

## What the operator has at the machine

| The operator needs | It is | It reaches the machine as |
|---|---|---|
| The program | `cam/<model>/o0042_op10_rev-a.nc` | `_outbox/<machine>/` |
| The setup: stock, fixture, zero, work offset, tools and their pockets | `cam/<model>/o0042_op10_rev-a_setup-sheet.pdf` | `_outbox/<machine>-docs/` |
| What to measure | `drawings/motor-mount_rev-a.pdf` | `_outbox/<machine>-docs/` |
| What to make today, and how many | a work order | not in the store; it records a job after it runs, not before |

**Knowing what is loaded.** Two things say which part and revision a
program is, and a release should have both:

1. The file name: `o0042_op10_rev-a.nc` gives the program number, the
   operation, and the revision.
2. A comment header in the program itself, starting on the program-number
   line. It travels with the program wherever it is copied, and it is in
   the copy kept with the job record.

   ```
   %
   O00042 (MOTOR MOUNT REV A OP10)
   (HAAS VF-2 - SETUP SHEET O0042_OP10_REV-A)
   (G54 - VISE, SOFT JAWS)
   (T1 1/2 END MILL - T2 SPOT DRILL - T3 NO 7 DRILL)
   ```

   Set it in CAM so the post writes it; in Fusion it is the program comment
   of the NC program. Do not add it by editing the posted file.

Nothing in the tools requires a setup sheet or a header yet; see
[issue #4](https://github.com/kgorman/MCAD_base/issues/4).

## Tormach, PathPilot: the store pushes

PathPilot shares its own G-code folder on the network as `gcode`. That is
the only share Tormach supports; the controller does not mount other
computers' shares. So the file goes to the machine, not the other way.

Once:

1. Put the controller on the shop network with an Ethernet cable.
2. In PathPilot's Settings tab, give the controller a network name, for
   example `tormach`.
3. From the computer that holds the store, connect to `smb://tormach/gcode`
   (macOS: Finder, Go, Connect to Server). On Windows, map `\\tormach\gcode`
   as a drive.

At each release:

1. Copy the outbox into the machine's share:

   ```sh
   cp /path/to/store/_outbox/tormach-pcnc440/*.nc /Volumes/gcode/
   ```

2. At the machine, open the File tab. The program is under Controller
   Files. Load it, read the header against the setup sheet, and run.

The controller has to be on to receive the copy. A one-line script run at
release, or a folder sync that mirrors `_outbox/tormach-pcnc440/` to
`\\tormach\gcode` whenever the machine is up, makes it automatic.

**The other way: mount the store on PathPilot.** Unsupported, but people do
it and it works. PathPilot is Linux underneath, and a line in `/etc/fstab`
mounts a NAS folder so it appears as a folder in the File tab:

```
//nas/store/_outbox/tormach-pcnc440  /home/operator/gcode/store  cifs  username=tormach,password=...,ro,uid=1000,_netdev,nofail  0 0
```

Mounted read-only, PathPilot then reads programs straight from the store
with no copying step, and the machine never has a stale file. Tormach's
position is that it "currently supports only the single shared folder that
we automatically share to the entire network," and that changes like this
are unsupported and could affect the controller's stability. A PathPilot
update may undo it. Keep the push method as the fallback.

## Haas, Next Generation Control: the machine pulls

The NGC has Remote Net Share: the control mounts a share on a PC or NAS
and shows it as a device in the program list. The share is read where it
is, so the machine always sees the current outbox.

On the NAS, once:

1. Make a share named `haas-vf2` whose folder is `<root>/_outbox/haas-vf2`.
   The Haas setting takes a share name, not a path, and it must have no
   spaces. Pointing a share at the outbox folder is what makes the deep,
   spaced paths in the store a non-issue.
2. Make a user `haas` with read-only rights to that share.
3. Allow SMB 2. Controls on software 100.18.000.1020 or later speak SMBv2;
   an older control needs SMBv1 allowed on the NAS, or a software update.

On the control, once. Settings, Network, Wired: `Wired Network Enabled` On,
an address from DHCP or a fixed one, F4 to apply. Then Settings, Network,
Net Share:

| Setting | Value |
|---|---|
| Remote Net Share Enabled | On |
| Remote Server Name | the NAS's name, or its IP address if the name does not resolve |
| Remote Share Path | `haas-vf2` (Setting 908; no spaces) |
| Remote User Name / Password | `haas` and its password |
| Workgroup | the NAS's workgroup, usually `WORKGROUP` |
| Enable SMBv1 Support | Off, unless the NAS only offers SMB 1 |

Press F4. The Remote Net Share status should read UP.

At each release: nothing at the machine. The outbox refill on the NAS is
the whole step. At the control: LIST PROGRAM, choose the Net Share device,
pick `o0042_op10_rev-a.nc`, and copy it into Memory with F2 or select it to
run. The program number inside the file, `O00042`, is what the control
uses.

On the Net Share device the list shows file names only, which is why the
revision is in the name. Once the program is in Memory, the list has an
`O #` column and a `Comment` column, and the comment is the one on the
program's first line: `MOTOR MOUNT REV A OP10`.

Older Haas controls without the NGC have no network share. For those it is
the USB stick, or DNC over RS-232 from a PC that reads the outbox.

## Side by side

| | Tormach PCNC 440 (PathPilot) | Haas VF-2 (NGC) |
|---|---|---|
| Who connects to whom | The store's computer connects to the machine's `gcode` share and copies in | The machine connects to the NAS share and reads |
| What the machine sees | A copy, current as of the last push | The outbox itself, always current |
| Supported by the maker | Yes for the push; the fstab mount is not | Yes, Remote Net Share is a built-in feature |
| Machine off at release time | Push fails; repeat it, or let a sync retry | Nothing to do; it reads the new file next time |
| Program file | `.nc` from the PathPilot post | `.nc` from the Haas NGC post, `O`-number inside |
| Setup sheet and drawing | `_outbox/tormach-pcnc440-docs/`, on a tablet or PC | `_outbox/haas-vf2-docs/`, on a tablet or PC |

Afterwards, either way, the operator records the run in
`jobs/<date>_<machine>_<job>/` with a copy of the file that ran and a
`job.json` naming `rev-a` and the machine model. See
[Record a job](../README.md#record-a-job).

## References

- Tormach forum, [Accessing files on NAS](https://forums.tormach.com/t/accessing-files-on-nas/789):
  Tormach's support statement, and the fstab CIFS mount people use.
- Tormach knowledge base, [Windows 10 Network Setup and G-Code Share](https://knowledgebase.tormach.com/770mx/windows-10-network-setup-and-g-code-share):
  the `gcode` share and mapping it from a PC.
- Tormach, [Connect your Tormach to the internet, ditch flash drives](https://tormach.com/articles/connect-your-tormach-to-the-internet-ditch-flash-drives).
- Haas service manual, [NGC Networking](https://www.haascnc.com/service/online-manuals/next-gen-control-electrical---service-manual/ngc---wire---wireless-networking.html):
  Remote Net Share and its settings.
- Haas, [Networking Troubleshooting Guide, NGC (TG105)](https://www.haascnc.com/service/troubleshooting-and-how-to/troubleshooting/networking-troubleshooting-guide---ngc.html):
  SMBv2 from software 100.18.000.1020, the Enable SMBv1 Support setting.
- Haas mill operator's manual, [Device Manager](https://www.haascnc.com/service/online-operator-s-manuals/mill-operator-s-manual/mill---device-manager.html):
  the `O #` and `Comment` columns, shown in the Memory tab only.
- [Haas Setting 908, Remote Share Path](https://www.helmancnc.com/haas-setting-908-remote-share-path/): no spaces in the path.
- Shop Floor Automations, [How to configure Haas NGC network settings](https://support.shopfloorautomations.com/portal/en/kb/articles/how-to-configure-haas-ngc-network-settings).
