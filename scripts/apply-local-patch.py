#!/usr/bin/env python3
"""Local patch set for the LiuHuanghai self-hosted RustDesk 1.5.3 fork.

1) secure_tcp / secure_tcp_silent: legacy hbbs (1.1.16) never answers the key
   exchange, so skip it instead of surfacing READ_TIMEOUT.
2) Self-hosted update channel served from https://www.mycloudserver.top/rustdesk
   and trusting our own ed25519 key.
3) Desktop update card: drop the "rustdesk" URI-prefix gate and point the
   download / changelog links at our own release page.

Usage: python3 apply-local-patch.py <repo-root>
Exit code 0 only when every anchor is present or already patched.
"""
import sys
import os

HOST = "www.mycloudserver.top"
REPO_PATH = "rustdesk"
API_URL = "https://%s/%s/latest.json" % (HOST, REPO_PATH)
PAGE_PREFIX = "https://%s/%s/" % (HOST, REPO_PATH)
DOWNLOAD_PREFIX = "https://%s/%s/" % (HOST, REPO_PATH)
KEY_ID = "2026-ed25519-main"
PUBLIC_KEY = [
    226, 165, 21, 41, 96, 23, 147, 120, 83, 79, 144, 96, 106, 87, 114, 43,
    200, 159, 21, 58, 21, 242, 220, 9, 235, 233, 67, 56, 138, 236, 111, 245,
]

# ---------------- secure_tcp patch ----------------
SECURE_OLD1 = "    secure_tcp_impl(conn, key, true).await\n}"
SECURE_NEW1 = (
    "    // [local patch] legacy hbbs never answers the key exchange; skip it\n"
    "    let _ = (conn, key);\n"
    "    Ok(())\n}"
)
SECURE_OLD2 = "    secure_tcp_impl(conn, key, false).await\n}"
SECURE_NEW2 = "    let _ = (conn, key);\n    Ok(())\n}"

# ---------------- update channel: host whitelist ----------------
OLD_HOST_CHECK = 'if url.scheme() != "https" || url.host_str() != Some("github.com") {'
NEW_HOST_CHECK = 'if url.scheme() != "https" || url.host_str() != Some("%s") {' % HOST

# ---------------- update channel: path shape ----------------
OLD_TAG_BRANCHES = (
    '        ["rustdesk", "rustdesk", "releases", "tag", release_id] => *release_id,\n'
    '        ["rustdesk", "rustdesk", "releases", "tag", release_id, ""] => *release_id,\n'
)
NEW_TAG_BRANCHES = (
    '        ["%s", release_id] => *release_id,\n'
    '        ["%s", release_id, ""] => *release_id,\n'
) % (REPO_PATH, REPO_PATH)

# ---------------- update check rewrite ----------------
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

// [local patch] update source is our own HTTPS host, no need to accept invalid certs.
#[tokio::main(flavor = "current_thread")]
pub async fn do_check_software_update() -> hbb_common::ResultType<()> {
    let url = SELF_HOSTED_UPDATE_API.to_owned();
''' % (HOST, REPO_PATH, API_URL)

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

OLD_BODY = '''            } else {
                response.bytes().await.map_err(|err| err.into())
            }
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

OLD_FINAL = "    let result = response_bytes.and_then(process_software_update_check_response);\n"
NEW_FINAL = "    let result = response_bytes;\n"

# ---------------- artifact download prefix ----------------
OLD_DL_PREFIX = '        format!("https://github.com/rustdesk/rustdesk/releases/download/{release_id}/");'
NEW_DL_PREFIX = '        format!("%s{release_id}/");' % DOWNLOAD_PREFIX

# ---------------- download filename resolver ----------------
OLD_RESOLVER = '''    if !url.starts_with("https://github.com/")
        || parsed.scheme() != "https"
        || parsed.host_str() != Some("github.com")
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.port().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return None;
    }

    let mut segments = parsed.path_segments()?;
    let owner = segments.next()?;
    let repo = segments.next()?;
    let releases = segments.next()?;
    let download = segments.next()?;
    let tag = segments.next()?;
    let filename = segments.next()?;

    if owner != "rustdesk"
        || repo != "rustdesk"
        || releases != "releases"
        || download != "download"
        || tag.is_empty()
        || segments.next().is_some()
        || !is_plain_update_filename(filename)
    {
        return None;
    }
'''

NEW_RESOLVER = '''    if !url.starts_with("%s/")
        || parsed.scheme() != "https"
        || parsed.host_str() != Some("%s")
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.port().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return None;
    }

    let mut segments = parsed.path_segments()?;
    let repo = segments.next()?;
    let tag = segments.next()?;
    let filename = segments.next()?;

    if repo != "%s"
        || tag.is_empty()
        || segments.next().is_some()
        || !is_plain_update_filename(filename)
    {
        return None;
    }
''' % (DOWNLOAD_PREFIX.rstrip("/"), HOST, REPO_PATH)

# ---------------- unit tests kept green ----------------
OLD_TEST_PARSE = '        let update_url = "https://github.com/rustdesk/rustdesk/releases/tag/v1.4.6";'
NEW_TEST_PARSE = '        let update_url = "https://%s/%s/v1.4.6";' % (HOST, REPO_PATH)

OLD_TEST_REJECT = '''            "http://github.com/rustdesk/rustdesk/releases/tag/v1.4.6",
            "https://github.com:8443/rustdesk/rustdesk/releases/tag/v1.4.6",
            "https://github.com:443/rustdesk/rustdesk/releases/tag/v1.4.6",
            "https://user@github.com/rustdesk/rustdesk/releases/tag/v1.4.6",
            "https://example.com/rustdesk/rustdesk/releases/tag/v1.4.6",
            "https://github.com/other/rustdesk/releases/tag/v1.4.6",
            "https://github.com/rustdesk/other/releases/tag/v1.4.6",
            "https://github.com/rustdesk/rustdesk/releases/download/v1.4.6/rustdesk.exe",
            "https://github.com/rustdesk/rustdesk/releases/tag/v1.4.6?x=1",
            "https://github.com/rustdesk/rustdesk/releases/tag/v1.4.6#asset",
            "https://github.com/rustdesk/rustdesk/releases/tag/v1.4.6/extra",
            "https://github.com/rustdesk/rustdesk/releases/tag/v1%2F4%2F6",
            "https://github.com/rustdesk/rustdesk/releases/tag/%2e%2e",
'''

NEW_TEST_REJECT = '''            "http://%(h)s/%(r)s/v1.4.6",
            "https://%(h)s:8443/%(r)s/v1.4.6",
            "https://%(h)s:443/%(r)s/v1.4.6",
            "https://user@%(h)s/%(r)s/v1.4.6",
            "https://example.com/%(r)s/v1.4.6",
            "https://%(h)s/other/v1.4.6",
            "https://%(h)s/%(r)s/v1.4.6/rustdesk.exe",
            "https://%(h)s/%(r)s/v1.4.6?x=1",
            "https://%(h)s/%(r)s/v1.4.6#asset",
            "https://%(h)s/%(r)s/v1.4.6/extra",
            "https://%(h)s/%(r)s/v1%%2F4%%2F6",
            "https://%(h)s/%(r)s/%%2e%%2e",
''' % {"h": HOST, "r": REPO_PATH}

OLD_TEST_ACCEPT = '''            "https://github.com/rustdesk/rustdesk/releases/download/1.4.0/rustdesk-1.4.0-x86_64.dmg",
'''
NEW_TEST_ACCEPT = '''            "https://%s/%s/1.4.0/rustdesk-1.4.0-x86_64.dmg",
''' % (HOST, REPO_PATH)

OLD_TEST_REJECT2 = '''            "http://github.com/rustdesk/rustdesk/releases/download/1/rustdesk.exe",
            "https://example.com/rustdesk.exe",
            "https://github.com/other/project/releases/download/1/rustdesk.exe",
            "https://github.com/rustdesk/rustdesk/releases/download/1/",
            "https://github.com/rustdesk/rustdesk/releases/download/1/nested/rustdesk.exe",
            "https://github.com/rustdesk/rustdesk/releases/download/1/C:rustdesk.exe",
            "https://user@github.com/rustdesk/rustdesk/releases/download/1/rustdesk.exe",
            "https://github.com:443/rustdesk/rustdesk/releases/download/1/rustdesk.exe",
            "https://github.com/rustdesk/rustdesk/releases/download/1/rustdesk.exe?download=1",
            "https://github.com/rustdesk/rustdesk/releases/download/1/rustdesk.exe#download",
'''

NEW_TEST_REJECT2 = '''            "http://%(h)s/%(r)s/1/rustdesk.exe",
            "https://example.com/rustdesk.exe",
            "https://%(h)s/other/1/rustdesk.exe",
            "https://%(h)s/%(r)s/1/",
            "https://%(h)s/%(r)s/1/nested/rustdesk.exe",
            "https://%(h)s/%(r)s/1/C:rustdesk.exe",
            "https://user@%(h)s/%(r)s/1/rustdesk.exe",
            "https://%(h)s:443/%(r)s/1/rustdesk.exe",
            "https://%(h)s/%(r)s/1/rustdesk.exe?download=1",
            "https://%(h)s/%(r)s/1/rustdesk.exe#download",
''' % {"h": HOST, "r": REPO_PATH}

# ---------------- trusted update key ----------------
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

# ---------------- desktop update card ----------------
OLD_CARD_COND = """    if (!bind.isCustomClient() &&
        updateUrl.isNotEmpty &&
        !isCardClosed &&
        bind.mainUriPrefixSync().contains('rustdesk')) {"""
NEW_CARD_COND = """    if (!bind.isCustomClient() &&
        updateUrl.isNotEmpty &&
        !isCardClosed) {"""

OLD_DL_URL = "        final Uri url = Uri.parse('https://rustdesk.com/download');"
NEW_DL_URL = "        final Uri url = Uri.parse('%s');" % PAGE_PREFIX

OLD_CHANGELOG = "              ? 'https://github.com/rustdesk/rustdesk/releases/tag/${bind.mainGetNewVersion()}'"
NEW_CHANGELOG = "              ? '%s'" % PAGE_PREFIX

# ---------------- version bump ----------------
OLD_CARGO_VER = 'name = "rustdesk"\nversion = "1.5.3"'
NEW_CARGO_VER = 'name = "rustdesk"\nversion = "1.5.4"'

PATCHES = [
    ("src/common.rs", "secure_tcp", SECURE_OLD1, SECURE_NEW1),
    ("src/common.rs", "secure_tcp_silent", SECURE_OLD2, SECURE_NEW2),
    ("src/common.rs", "update-host-whitelist", OLD_HOST_CHECK, NEW_HOST_CHECK),
    ("src/common.rs", "update-tag-whitelist", OLD_TAG_BRANCHES, NEW_TAG_BRANCHES),
    ("src/common.rs", "update-check-head", OLD_CHECK_HEAD, NEW_CHECK_HEAD),
    ("src/common.rs", "update-check-post", OLD_POST, NEW_POST),
    ("src/common.rs", "update-check-retry", OLD_RETRY, NEW_RETRY),
    ("src/common.rs", "update-check-body", OLD_BODY, NEW_BODY),
    ("src/common.rs", "update-check-final", OLD_FINAL, NEW_FINAL),
    ("src/common.rs", "unit-test-parse-url", OLD_TEST_PARSE, NEW_TEST_PARSE),
    ("src/common.rs", "unit-test-reject-url", OLD_TEST_REJECT, NEW_TEST_REJECT),
    ("src/updater/artifact.rs", "update-download-prefix", OLD_DL_PREFIX, NEW_DL_PREFIX),
    ("src/updater/artifact.rs", "update-download-resolver", OLD_RESOLVER, NEW_RESOLVER),
    ("src/updater/artifact.rs", "unit-test-accept", OLD_TEST_ACCEPT, NEW_TEST_ACCEPT),
    ("src/updater/artifact.rs", "unit-test-reject", OLD_TEST_REJECT2, NEW_TEST_REJECT2),
    ("src/update_metadata.rs", "update-trusted-key", OLD_KEYS, NEW_KEYS),
    ("Cargo.toml", "bump-version-1.5.4", OLD_CARGO_VER, NEW_CARGO_VER),
    ("Cargo.lock", "bump-lock-version-1.5.4", OLD_CARGO_VER, NEW_CARGO_VER),
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
