# Publishing Local Audio Zones

The suggested repository is `alphasixtyfive/local-audio-zones`. Keep the app's
own name, slug and 0.1.0 version, and retain the existing Git history so the
upstream origin remains visible. Publish to a separate remote; the current
`origin` belongs to Music Assistant.

The source is ready for a community repository, but a public release should
wait for the final Linux runtime checks and a full reboot/listening test on
the installed hardware. The app remains experimental. No claim of universal
soundcard compatibility is appropriate.

Before publishing:

1. Create the chosen repository and set its default branch to `main`.
2. Add a separate Git remote and push the reviewed branch to `main`.
3. Add root `repository.yaml` with the repository name, URL and maintainer;
   set the app manifest's `url` to the same verified repository URL.
4. Run both architecture jobs. Tag `v0.1.0` only after they pass. The existing
   release workflow publishes architecture images and a multi-architecture
   manifest to `ghcr.io/<owner>/<repository>:0.1.0`.
5. Make the GHCR package public, then set the app manifest's `image` to the
   generic GHCR name. Verify a clean installation from the repository URL.

Keep `LICENSE`, `NOTICE`, `HASSCONNECT-LICENSE`, upstream notices, modification
notices and the patch explanation with the source and image. The app guide
credits Music Assistant Local Audio, PR 34 and the pinned Sendspin player.
Household device paths, credentials, state snapshots and work-directory
diagnostics do not belong in the published repository.

The editable artwork is `local_audio_zones/icon.svg`; `icon.png` and `logo.png`
are transparent PNG exports at 128 and 256 pixels. They reuse HASSConnect's
cyan/white palette with a different interior symbol.

Sources: [Home Assistant repositories](https://developers.home-assistant.io/docs/apps/repository/),
[publishing images](https://developers.home-assistant.io/docs/apps/publishing/),
[icons and logos](https://developers.home-assistant.io/docs/apps/presentation/).
