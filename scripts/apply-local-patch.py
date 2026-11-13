#!/usr/bin/env python3
"""Local patch set for LiuHuanghai928/rustdesk fork (based on upstream 1.5.3).

1) secure_tcp / secure_tcp_silent: legacy hbbs never answers the key exchange,
   so skip it instead of surfacing READ_TIMEOUT.
2) Self-hosted update channel: update check + artifact download point at
   github.com/LiuHuanghai928/rustdesk, with our own ed25519 key.
3) Update card in the home page must show for self-hosted servers too.
4) Bump package version to 1.5.4 so older 1.5.3 clients can detect the update.

Usage: python3 apply-local-patch.py <repo-root>
Exit code 0 only when every anchor is present or already patched.
"""
import sys
import os

OWNER = "LiuHuanghai928"
REPO = "rustdesk"
API_URL = "https://api.github.com/repos/%s/%s/releases?per_page=20" % (OWNER, REPO)
PAGE_PREFIX = "https://github.com/%s/%s/releases/tag/" % (OWNER, REPO)
DOWNLOAD_PREFIX = "https://github.com/%s/%s/releases/download/" % (OWNER, REPO)
RELEASES_PAGE = "https://github.com/%s/%s/releases" % (OWNER, REPO)
KEY_ID = "2026-ed25519-main"
PUBLIC_KEY = [
    226, 165, 21, 41, 96, 23, 147, 120, 83, 79, 144, 96, 106, 87, 114, 43,
    200, 159, 21, 58, 21, 242, 220, 9, 235, 233, 67, 56, 138, 236, 111, 245,
]

SECURE_OLD1 = "    secure_tcp_impl(conn, key, true).await\n}"
SECURE_NEW1 = (
    "    // [local patch] legacy hbbs never answers the key exchange; skip it\n"
    "    let _ = (conn, key);\n"
    "    Ok(())\n}"
)
SECURE_OLD2 = "    secure_tcp_impl(conn, key, false).await\n}"
SECURE_NEW2 = "    let _ = (conn, key);\n    Ok(())\n}"

OLD_TAG_BRANCHES = (
    '        ["rustdesk", "rustdesk", "releases", "tag", release_id] => *release_id,\n'
    '        ["rustdesk", "rustdesk", "releases", "tag", release_id, ""] => *release_id,\n'
)
NEW_TAG_BRANCHES = (
    '        ["%s", "%s", "releases", "tag", release_id] => *release_id,\n'
    '        ["%s", "%s", "releases", "tag", release_id, ""] => *release_id,\n'
) % (OWNER, REPO, OWNER, REPO)

OLD_CHECK_HEAD = '''// No need to check `danger_accept_invalid_cert` for now.
// Because the url is always `https://api.rustdesk.com/version/latest`.
#[tokio::main(flavor = "current_thread")]
pub async fn do_check_software_update() -> hbb_common::ResultType<()> {
    let (request, url) =
        hbb_common::version_check_request(hbb_common::VER_TYPE_RUSTDESK_CLIENT.to_string());
'''

NEW_CHECK_HEAD = '''// [local patch] self-hosted update channel: %s/%s
pub(crate) const SELF_HOSTED_UPDATE_API: &str = "%s";

fn apply_software_update_page_url(response_url: &str) -> ResultType<()> {
    let release_id = release_id_from_update_url(response_url)?;
    let latest_release_version = display_version_from_release_id(&release_id)?;
    if get_version_number(&latest_release_version) > get_version_number(crate::VERSION) {
        #[cfg(feature = "flutter")]
        {
            let mut m = HashMap::new();
            m.insert("name", "check_software_update_finish");
            m.insert("url", response_url);
            if let Ok(data) = serde_json::to_string(&m) {
                let _ = crate::flutter::push_global_event(crate::flutter::APP_TYPE_MAIN, data);
            }
        }
        *SOFTWARE_UPDATE_URL.lock().unwrap() = response_url.to_owned();
    } else {
        clear_software_update_url();
    }
    Ok(())
}

// [local patch] the update source is GitHub over TLS, no need to accept invalid certs.
#[tokio::main(flavor = "current_thread")]
pub async fn do_check_software_update() -> hbb_common::ResultType<()> {
    let url = SELF_HOSTED_UPDATE_API.to_owned();
''' % (OWNER, REPO, API_URL)

OLD_POST = '''    let client = create_http_client_async(tls_type, false);
    let latest_release_response: ResultType<_> = match client.post(&url).json(&request).send().await
    {
'''
NEW_POST = '''    let client = create_http_client_async(tls_type, false);
    let latest_release_response: ResultType<_> = match client
        .get(&url)
        .header("User-Agent", "rustdesk-update-check")
        .send()
        .await
    {
'''

OLD_RETRY = '''                let client = create_http_client_async(tls_type, false);
                match client.post(&url).json(&request).send().await {
'''
NEW_RETRY = '''                let client = create_http_client_async(tls_type, false);
                match client
                    .get(&url)
                    .header("User-Agent", "rustdesk-update-check")
                    .send()
                    .await
                {
'''

NEW_BODY = '''            } else {
                match response.json::<serde_json::Value>().await {
                    Ok(body) => {
                        let mut page_url = String::new();
                        if let Some(list) = body.as_array() {
                            for item in list.iter() {
                                let tag = item
                                    .get("tag_name")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or_default();
                                if tag.is_empty() {
                                    continue;
                                }
                                let candidate = format!("{}{}", "%s", tag);
                                if release_id_from_update_url(&candidate).is_ok()
                                    && display_version_from_release_id(tag).is_ok()
                                {
                                    page_url = candidate;
                                    break;
                                }
                            }
                        }
                        if page_url.is_empty() {
                            Err(anyhow!("Software update check failed: no matching release tag found"))
                        } else {
                            apply_software_update_page_url(&page_url)
                        }
                    }
                    Err(err) => Err(err.into()),
                }
            }
''' % PAGE_PREFIX

OLD_BODY = '''            } else {
                response.bytes().await.map_err(|err| err.into())
            }
'''

OLD_FINAL = "    let result = response_bytes.and_then(process_software_update_check_response);\n"
NEW_FINAL = "    let result = response_bytes;\n"

OLD_DL_PREFIX = '        format!("https://github.com/rustdesk/rustdesk/releases/download/{release_id}/");'
NEW_DL_PREFIX = '        format!("%s{release_id}/");' % DOWNLOAD_PREFIX

OLD_OWNER = '''    if owner != "rustdesk"
        || repo != "rustdesk"
'''
NEW_OWNER = '''    if owner != "%s"
        || repo != "%s"
''' % (OWNER, REPO)

OLD_KEYS = '''const TRUSTED_UPDATE_KEYS: &[TrustedUpdateKey] = &[TrustedUpdateKey {
    key_id: "2026-ed25519-main",
    algorithm: SIGNATURE_ALGORITHM,
    public_key: [
        52, 214, 32, 65, 191, 134, 137, 75, 157, 74, 67, 221, 150, 161, 118, 215, 218, 35, 236,
        156, 127, 120, 133, 156, 197, 141, 37, 22, 47, 106, 181, 177,
    ],
}];'''

NEW_KEYS = '''const TRUSTED_UPDATE_KEYS: &[TrustedUpdateKey] = &[TrustedUpdateKey {
    key_id: "%s",
    algorithm: SIGNATURE_ALGORITHM,
    public_key: [
%s,
    ],
}];''' % (
    KEY_ID,
    ",\n".join(
        "        " + ", ".join(str(b) for b in PUBLIC_KEY[i:i + 8])
        for i in range(0, len(PUBLIC_KEY), 8)
    ),
)

OLD_CARD_COND = '''    if (!bind.isCustomClient() &&
        updateUrl.isNotEmpty &&
        !isCardClosed &&
        bind.mainUriPrefixSync().contains('rustdesk')) {'''
NEW_CARD_COND = '''    if (!bind.isCustomClient() &&
        updateUrl.isNotEmpty &&
        !isCardClosed) {'''

OLD_DL_URL = "        final Uri url = Uri.parse('https://rustdesk.com/download');"
NEW_DL_URL = "        final Uri url = Uri.parse('%s');" % RELEASES_PAGE

OLD_CHANGELOG = (
    "              ? 'https://github.com/rustdesk/rustdesk/releases/tag/"
    "${bind.mainGetNewVersion()}'"
)
NEW_CHANGELOG = "              ? '%s'" % RELEASES_PAGE

OLD_CARGO_VER = 'name = "rustdesk"\nversion = "1.5.3"'
NEW_CARGO_VER = 'name = "rustdesk"\nversion = "1.5.4"'

PATCHES = [
    ("src/common.rs", "secure_tcp", SECURE_OLD1, SECURE_NEW1),
    ("src/common.rs", "secure_tcp_silent", SECURE_OLD2, SECURE_NEW2),
    ("src/common.rs", "update-tag-whitelist", OLD_TAG_BRANCHES, NEW_TAG_BRANCHES),
    ("src/common.rs", "update-check-head", OLD_CHECK_HEAD, NEW_CHECK_HEAD),
    ("src/common.rs", "update-check-post", OLD_POST, NEW_POST),
    ("src/common.rs", "update-check-retry", OLD_RETRY, NEW_RETRY),
    ("src/common.rs", "update-check-body", OLD_BODY, NEW_BODY),
    ("src/common.rs", "update-check-final", OLD_FINAL, NEW_FINAL),
    ("src/updater/artifact.rs", "update-download-prefix", OLD_DL_PREFIX, NEW_DL_PREFIX),
    ("src/updater/artifact.rs", "update-owner-check", OLD_OWNER, NEW_OWNER),
    ("src/update_metadata.rs", "update-trusted-key", OLD_KEYS, NEW_KEYS),
    ("Cargo.toml", "bump-version-1.5.4", OLD_CARGO_VER, NEW_CARGO_VER),
    (
        "flutter/lib/desktop/pages/desktop_home_page.dart",
        "update-card-visible",
        OLD_CARD_COND,
        NEW_CARD_COND,
    ),
    (
        "flutter/lib/desktop/pages/desktop_home_page.dart",
        "update-card-download-url",
        OLD_DL_URL,
        NEW_DL_URL,
    ),
    (
        "flutter/lib/desktop/pages/desktop_home_page.dart",
        "update-card-changelog",
        OLD_CHANGELOG,
        NEW_CHANGELOG,
    ),
]


def main(root):
    failures = []
    for rel, name, old, new in PATCHES:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            failures.append("%s: missing file" % rel)
            continue
        with open(path, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        if old in text:
            text = text.replace(old, new, 1)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            print("OK   %-26s %s" % (name, rel))
        elif new in text:
            print("SKIP %-26s %s (already applied)" % (name, rel))
        else:
            failures.append("%s: anchor not found (%s)" % (name, rel))
    if failures:
        for item in failures:
            print("FAIL " + item)
        return 1
    print("all local patches applied")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: apply-local-patch.py <repo-root>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
