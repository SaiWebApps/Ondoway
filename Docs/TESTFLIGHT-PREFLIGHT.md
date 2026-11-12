# TestFlight Preflight Checklist

Run through this list before executing `make testflight`.

---

## Pre-flight Checks

- [ ] **Apple Developer Program** — membership active (not expired). Check at https://developer.apple.com/account
- [ ] **Distribution certificate** — valid iOS Distribution cert in Keychain Access. Filter by "iOS Distribution" in login keychain; check expiry date.
- [ ] **Provisioning profile** — App Store distribution profile matches bundle ID `com.ondoway.app`. Check in Xcode > Settings > Accounts > Manage Certificates, or download from Developer Portal.
- [ ] **App Store Connect** — app registered with matching bundle ID. At least one version must exist (even if incomplete).
- [ ] **API key configured** — the `.p8` file is on this machine and its two IDs are in the Render environment. See First-time Setup below.
- [ ] **Build number** — will auto-increment via `agvtool next-version -all`. No manual action needed.
- [ ] **Proxy whitelist** — `contentdelivery.itunes.apple.com` and `itunesconnect.apple.com` must bypass corporate proxy.

---

## First-time Setup

`make testflight` uploads with an App Store Connect API key. It reads the key's two
IDs, `APP_STORE_API_KEY_ID` and `APP_STORE_ISSUER_ID`, from the Render service
environment on every run. You do not export them yourself.

1. App Store Connect > Users and Access > Integrations > App Store Connect API
2. Generate a key with "App Manager" role or higher
3. Download the `.p8` file, place in `~/.private_keys/AuthKey_<KEY_ID>.p8`
4. Add `APP_STORE_API_KEY_ID` and `APP_STORE_ISSUER_ID` to the Render service environment
5. Authorise this machine to read Render once: `make render-auth-setup`

---

## Running

```bash
make testflight
```

This will:
1. Fetch the Render environment and stop if either API key ID is missing
2. Bump the build number (agvtool)
3. Build the release IPA (flutter build ipa, pointing at production API)
4. Upload to App Store Connect via `xcrun altool` with the API key

After upload succeeds, allow ~15 minutes for Apple's processing before the build appears in TestFlight.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "No signing certificate" | Missing or expired dist cert | Xcode > Settings > Accounts > Manage Certificates > + iOS Distribution |
| "No applicable devices found" | Wrong provisioning profile type | Ensure profile is "App Store" type, not Ad Hoc or Development |
| "Unable to upload" / connection timeout | Proxy blocking iTunes domains | Add `contentdelivery.itunes.apple.com` and `itunesconnect.apple.com` to NO_PROXY |
| "The bundle version must be higher" | Build number not incremented | Should not happen (agvtool runs first), but check `mobile/ios/Runner.xcodeproj/project.pbxproj` for CURRENT_PROJECT_VERSION |
| "APP_STORE_API_KEY_ID missing." or "APP_STORE_ISSUER_ID missing." | The ID is not in the Render environment | Add it to the Render service environment; check access with `make render-auth-status` |
| "Authentication failed" | Wrong key or missing `.p8` file | Check the `.p8` file is at `~/.private_keys/AuthKey_<KEY_ID>.p8` and its key ID matches `APP_STORE_API_KEY_ID` |
| Upload succeeds but build not in TestFlight after 30 min | Apple processing issue or compliance problem | Check email for rejection notice; check App Store Connect > TestFlight > Processing |
