"""Report stable native-player releases without changing the build pins."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

UPSTREAM = "Sendspin/sendspin-cpp-cli"
ROOT = Path(__file__).resolve().parent.parent


def github(*arguments):
    result = subprocess.check_output(["gh", *arguments], text=True)
    return json.loads(result) if arguments[0] == "api" else result.strip()


def version(tag):
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag)
    if not match:
        raise ValueError("Expected a stable MAJOR.MINOR.PATCH release tag")
    return tuple(map(int, match.groups()))


def check(dockerfile, repository=None, notify=False):
    pins = {}
    for key in ("SENDSPIN_CLI_REF", "SENDSPIN_CLI_SHA"):
        values = re.findall(r"^ARG " + key + r"=(\S+)$", dockerfile, re.MULTILINE)
        if len(values) != 1:
            raise ValueError("Expected one " + key + " build pin")
        pins[key] = values[0]
    current = pins["SENDSPIN_CLI_REF"]
    current_version = version(current)
    if not re.fullmatch(r"[0-9a-f]{40}", pins["SENDSPIN_CLI_SHA"]):
        raise ValueError("Expected a complete native-player commit SHA")
    release = github("api", "repos/" + UPSTREAM + "/releases/latest")
    if release["draft"] or release["prerelease"]:
        raise ValueError("Expected a published stable release")
    latest = release["tag_name"]
    newer = version(latest) > current_version
    summary = ("Pinned sendspin-cli: **" + current + "**.\n\n"
               + "Latest stable release: [" + latest + "](" + release["html_url"] + ").\n\n"
               + ("A native-player update needs review." if newer else "No newer stable release is available."))
    if not newer or not notify:
        return summary
    if not repository or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("A GitHub repository is required to create an update issue")
    title = "Update sendspin-cli to " + latest
    pages = github("api", "--paginate", "--slurp",
                   "repos/" + repository + "/issues?state=all&per_page=100")
    for page in pages:
        for issue in page:
            if "pull_request" not in issue and issue["title"] == title:
                return summary + "\n\nTracked in [the existing issue](" + issue["html_url"] + ")."
    body = ("The app pins sendspin-cli **" + current + "**; [" + latest + "](" + release["html_url"] + ") is available.\n\n"
            + "[Compare changes](https://github.com/" + UPSTREAM + "/compare/" + current + "..." + latest + ").\n\n"
            + "- Update the native tag and verified commit SHA together.\n"
            + "- Review both local audio patches and the pinned core commit; refresh dependency notices when needed.\n"
            + "- Pass the amd64 and aarch64 runtime checks before releasing.\n")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "issue.md"
        path.write_text(body, encoding="utf-8")
        url = github("issue", "create", "--repo", repository, "--title", title,
                     "--label", "dependencies", "--body-file", str(path))
    return summary + "\n\nCreated [an update issue](" + url + ")."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notify", action="store_true", help="Create an issue for a newer stable player")
    arguments = parser.parse_args()
    summary = check((ROOT / "local_audio_zones/Dockerfile").read_text(),
                    os.environ.get("GITHUB_REPOSITORY"), arguments.notify)
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as target:
            target.write(summary + "\n")


if __name__ == "__main__":
    main()
