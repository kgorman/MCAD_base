# Getting programs to machines: two worked examples

A machine runs a file from a released revision, never from `wip/`. How the
file gets there depends on what the control can do. This document walks
through two common cases: a Tormach running PathPilot, which can only serve
files, and a Haas with the Next Generation Control, which can mount a share.
The layout rules are in [CANONICAL_TREE.md](CANONICAL_TREE.md#getting-a-release-to-a-machine).

## The release both machines run

Posting from CAM gives one program per machine model. They go into the
revision under `cam/<model>/`, lowercase, no spaces, and the manifest lists
both models as proven on.

```
Kenny's Hub/Bike/Motor Mount/released/rev-a/
  manifest.json            "process": { "proven_on": ["tormach-pcnc440", "haas-vf2"] }
  SHA256SUMS
  cad/motor-mount.f3d
  cam/tormach-pcnc440/
    o0042_op10.nc          PathPilot post
    setup-sheet_op10.pdf
  cam/haas-vf2/
    o0042_op10.nc          Haas NGC post; the file starts with %, O00042
    setup-sheet_op10.pdf
```

Deep paths and names with spaces are fine for people and for the tools,
but not for every control. `add-machine` makes a flat folder per machine
under `_outbox/`, and at each release the current programs are copied into
it, replacing the old ones. Nothing is edited there; the copy in the
revision stays frozen and checksummed.

```sh
./mcad_tree.py add-machine /path/to/store tormach-pcnc440
./mcad_tree.py add-machine /path/to/store haas-vf2

cd "/path/to/store/Kenny's Hub/Bike/Motor Mount/released/rev-a"
cp cam/tormach-pcnc440/*.nc /path/to/store/_outbox/tormach-pcnc440/
cp cam/haas-vf2/*.nc        /path/to/store/_outbox/haas-vf2/
```

```
<root>/
  _outbox/
    tormach-pcnc440/
      o0042_op10.nc        <- the store pushes this to the Tormach
    haas-vf2/
      o0042_op10.nc        <- the Haas reads this over the network
```

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
   Files. Load it and run.

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
pick `o0042_op10.nc`, and copy it into Memory with F2 or select it to run.
The program number inside the file, `O00042`, is what the control uses.

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
- [Haas Setting 908, Remote Share Path](https://www.helmancnc.com/haas-setting-908-remote-share-path/): no spaces in the path.
- Shop Floor Automations, [How to configure Haas NGC network settings](https://support.shopfloorautomations.com/portal/en/kb/articles/how-to-configure-haas-ngc-network-settings).
