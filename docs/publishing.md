# Publishing

The app is published at
[alphasixtyfive/local-audio-zones](https://github.com/alphasixtyfive/local-audio-zones).
Keep `origin` pointing to Music Assistant and use the separate `community`
remote for this repository.

Before tagging a version:

1. Update the manifest version and changelog together.
2. Check the licenses and notices for the pinned player and its dependencies.
3. Push to `main` and wait for all Build jobs, including both architectures.
4. Test the outputs on real hardware. Include a full reboot when hardware
   routing or device identity changes.
5. Push only the new version tag. Do not copy upstream tags to this repository.

The Release workflow publishes both architectures and their shared manifest
at `ghcr.io/alphasixtyfive/local-audio-zones:<version>`. Check that the package
is public and can be pulled anonymously before referencing it in the app's
`image` setting. Test installation from the repository URL.

The app is experimental. Keep hardware and recovery limits in the
[hardware guide](audio-hardware-support.md).

Retain upstream history, licenses and [NOTICE](../local_audio_zones/NOTICE).
Keep household configuration, credentials, state and diagnostic logs outside
this repository.

The editable icon is `local_audio_zones/icon.svg`; PNG exports are 128 and
256 pixels.

See Home Assistant's [repository](https://developers.home-assistant.io/docs/apps/repository/)
and [publishing](https://developers.home-assistant.io/docs/apps/publishing/)
guides.
