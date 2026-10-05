# Publishing Local Audio Zones

The suggested repository is `alphasixtyfive/local-audio-zones`. Keep the app's
own name, slug and 0.1.0 version, and retain the existing Git history so the
upstream origin remains visible. Publish to a separate remote; the current
`origin` belongs to Music Assistant.

The community repository plan is prepared. Before a public release, complete
the bundled dependency notice review, both architecture checks and a full
reboot/listening test on the installed hardware. The app remains experimental. No claim of universal
soundcard compatibility is appropriate.

Before publishing:

1. Inspect the dependencies fetched by the pinned player build, including
   codec submodules. Retain their required licenses and notices in the source
   and final image; the player's Apache license does not cover every dependency.
2. Create the chosen repository and set its default branch to `main`.
3. Add a separate Git remote and push the reviewed branch to `main`.
4. Add root `repository.yaml` with the repository name, URL and maintainer;
   set the app manifest's `url` to the same verified repository URL.
5. Run both architecture jobs. Tag `v0.1.0` only after they pass. The existing
   release workflow publishes architecture images and a multi-architecture
   manifest to `ghcr.io/<owner>/<repository>:0.1.0`.
6. Make the GHCR package public, then set the app manifest's `image` to the
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
