# hypr-qa

QA for Hyprland apps, always in a VM. Built on
[vmkit](https://github.com/MasonRhodesDev/vmkit), which boots and drives the QEMU guest and records
its screen with every action on the same host clock. hypr-qa adds only the
Hyprland parts:

- **Guest profiles** (`profiles/`): cloud image + cloud-init that boot straight
  into a Hyprland session, always with QMP. First profile: `plain-hyprland`.
- **Session helpers**: push a binary, run commands inside the guest's Hyprland
  session, apply `hyprctl` keywords.
- **Scenarios** (`docs/scenario-schema.md`): TOML files of steps, where each step is an action
  plus checks on the frames recorded at set offsets after it. The run writes a
  report with a contact sheet per step, a pass/fail summary and the video.

Status: early. The scenario schema is a v0 draft; the runner and the first profile
are in progress.

VMs need KVM, so runs happen on a KVM host, never on the desktop you're using.

## License

MIT © Mason Rhodes
