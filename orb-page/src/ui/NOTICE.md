# The orb and UI parts

`components/`, `lib/` and `styles/` are the orb (`AiAvatar`) and the few UI parts the first run's window uses
(`Button`, `Progress`, `RadioGroup`, `Spinner`), taken from Glass Labs' design system and published here under
this repository's MIT license (`LICENSE` at the repository root).

They build on:

- **Ariakit** (`@ariakit/react`), MIT License. `RadioGroup` wraps Ariakit's radio, and `lib/ak/` mirrors a small
  subset of Ariakit's internal authoring helpers. https://github.com/ariakit/ariakit
- **Radix Colors**, MIT License. The color scales in `styles/colors/` are seeded from Radix's 12-step scales
  (gray is a retinted Radix Slate) and then adjusted. https://github.com/radix-ui/colors
- **vgpu**, MIT License: the orb's WebGPU renderer. https://github.com/vercel-labs/vgpu
- **clsx**, MIT License. https://github.com/lukeed/clsx
