import hashlib
import json
import os
import runpy
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path


RAW_REPO_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "ZoeRis/guozi-updates/main/"
)
CDN_REPO_BASE_URL = (
    "https://cdn.jsdelivr.net/gh/"
    "ZoeRis/guozi-updates@main/"
)

VERSION_URLS = (
    RAW_REPO_BASE_URL + "version.json",
    CDN_REPO_BASE_URL + "version.json",
)

MAX_VERSION_BYTES = 256 * 1024
MAX_APP_BYTES = 2 * 1024 * 1024
NETWORK_TIMEOUT = 6

# 未来如果 app.py 需要新的启动器能力，
# 先在线更新 launcher.py，再提高 manifest 里的 app_api。
SUPPORTED_APP_API = 1


def base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent


BASE_DIR = base_dir()
APP_FILE = BASE_DIR / "app.py"
APP_NEW_FILE = BASE_DIR / "app.py.new"
APP_BACKUP_FILE = BASE_DIR / "app.py.backup"
STATE_FILE = BASE_DIR / "launcher_state.json"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def current_file_hash(path):
    if not path.exists():
        return None

    try:
        return sha256_bytes(path.read_bytes())
    except OSError:
        return None


def download_bytes(url, max_bytes):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GuoziLauncherCore/2.0",
            "Cache-Control": "no-cache",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=NETWORK_TIMEOUT,
    ) as response:
        content_length = response.headers.get(
            "Content-Length"
        )

        if (
            content_length is not None
            and int(content_length) > max_bytes
        ):
            raise ValueError("下载文件过大。")

        data = response.read(max_bytes + 1)

    if len(data) > max_bytes:
        raise ValueError("下载文件过大。")

    if not data:
        raise ValueError("下载文件为空。")

    return data


def download_from_sources(urls, max_bytes):
    last_error = None

    for url in urls:
        try:
            return download_bytes(url, max_bytes)
        except (
            OSError,
            ValueError,
            urllib.error.URLError,
            urllib.error.HTTPError,
        ) as error:
            last_error = error

    if last_error is not None:
        raise last_error

    raise urllib.error.URLError(
        "没有可用的下载线路。"
    )


def download_verified_from_sources(
    urls,
    max_bytes,
    expected_sha256,
    validator=None,
):
    """逐线路下载，只有 hash + 内容检查都通过才接受。"""

    last_error = None
    expected = expected_sha256.lower().strip()

    if len(expected) != 64:
        raise ValueError("SHA256 格式错误。")

    for url in urls:
        try:
            data = download_bytes(url, max_bytes)

            actual = sha256_bytes(data)

            if actual != expected:
                raise ValueError(
                    "下载文件 SHA256 不匹配。"
                )

            if validator is not None:
                validator(data)

            return data

        except (
            OSError,
            ValueError,
            urllib.error.URLError,
            urllib.error.HTTPError,
        ) as error:
            last_error = error

    if last_error is not None:
        raise last_error

    raise urllib.error.URLError(
        "没有通过校验的下载线路。"
    )


def trusted_app_urls(supplied_url):
    if supplied_url.startswith(RAW_REPO_BASE_URL):
        relative_path = supplied_url[
            len(RAW_REPO_BASE_URL):
        ]
    elif supplied_url.startswith(CDN_REPO_BASE_URL):
        relative_path = supplied_url[
            len(CDN_REPO_BASE_URL):
        ]
    else:
        raise ValueError("程序更新网址不安全。")

    if relative_path != "app.py":
        raise ValueError("程序更新文件路径不安全。")

    return (
        RAW_REPO_BASE_URL + "app.py",
        CDN_REPO_BASE_URL + "app.py",
    )


def read_state():
    if not STATE_FILE.exists():
        return {
            "app_version": 0,
            "app_sha256": None,
        }

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(data, dict):
            raise ValueError

        stored_hash = data.get("app_sha256")

        if not isinstance(stored_hash, str):
            stored_hash = None

        return {
            "app_version": int(
                data.get("app_version", 0)
            ),
            "app_sha256": stored_hash,
        }

    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        return {
            "app_version": 0,
            "app_sha256": None,
        }


def write_state(app_version, app_sha256):
    temp_file = (
        BASE_DIR / "launcher_state.json.new"
    )

    temp_file.write_text(
        json.dumps(
            {
                "app_version": int(app_version),
                "app_sha256": app_sha256,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    temp_file.replace(STATE_FILE)


def validate_app_bytes(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(
            "线上 app.py 不是 UTF-8。"
        ) from error

    compile(
        text,
        str(APP_NEW_FILE),
        "exec",
    )

    return text


def check_code_update():
    """校验并更新 app.py；失败时保留本地可用版本。"""

    previous_state = read_state()
    previous_version = previous_state[
        "app_version"
    ]

    try:
        version_bytes = download_from_sources(
            VERSION_URLS,
            MAX_VERSION_BYTES,
        )

        version_data = json.loads(
            version_bytes.decode("utf-8")
        )

        if not isinstance(version_data, dict):
            raise ValueError(
                "version.json 格式错误。"
            )

        remote_version = int(
            version_data.get("app_version", 0)
        )

        required_api = int(
            version_data.get("app_api", 1)
        )

        if required_api > SUPPORTED_APP_API:
            raise ValueError(
                "线上 app.py 需要更新的 launcher。"
            )

        expected_hash = str(
            version_data["app_sha256"]
        ).lower().strip()

        app_urls = trusted_app_urls(
            version_data["app_url"]
        )

        local_hash = current_file_hash(APP_FILE)

        # 同版本甚至更高版本时，也必须确认本地文件 hash 正确。
        # 这样 app.py 被误改、写坏或半截覆盖时可自动修复。
        if (
            APP_FILE.exists()
            and remote_version <= previous_version
            and local_hash == expected_hash
        ):
            if (
                previous_state.get("app_sha256")
                != expected_hash
            ):
                write_state(
                    max(
                        previous_version,
                        remote_version,
                    ),
                    expected_hash,
                )

            return (
                False,
                previous_state,
            )

        app_bytes = download_verified_from_sources(
            app_urls,
            MAX_APP_BYTES,
            expected_hash,
            validate_app_bytes,
        )

        APP_NEW_FILE.write_bytes(app_bytes)

        # 再校验一次实际落盘内容。
        written_bytes = APP_NEW_FILE.read_bytes()

        if sha256_bytes(written_bytes) != expected_hash:
            raise ValueError(
                "app.py 写入磁盘后校验失败。"
            )

        validate_app_bytes(written_bytes)

        if APP_FILE.exists():
            shutil.copy2(
                APP_FILE,
                APP_BACKUP_FILE,
            )

        APP_NEW_FILE.replace(APP_FILE)

        write_state(
            remote_version,
            expected_hash,
        )

        return (
            True,
            previous_state,
        )

    except (
        OSError,
        UnicodeDecodeError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        urllib.error.URLError,
        urllib.error.HTTPError,
    ) as error:
        print(
            "程序代码更新检查失败，"
            "继续使用本地版本：",
            repr(error),
        )

        try:
            APP_NEW_FILE.unlink(
                missing_ok=True
            )
        except OSError:
            pass

        return (
            False,
            previous_state,
        )


def restore_backup(previous_state):
    if not APP_BACKUP_FILE.exists():
        return False

    try:
        shutil.copy2(
            APP_BACKUP_FILE,
            APP_FILE,
        )

        previous_hash = current_file_hash(
            APP_FILE
        )

        write_state(
            previous_state.get(
                "app_version",
                0,
            ),
            previous_hash,
        )

        return True

    except OSError:
        return False


def run_app():
    if not APP_FILE.exists():
        raise FileNotFoundError(
            "找不到 app.py，且线上下载失败。"
        )

    os.chdir(BASE_DIR)

    runpy.run_path(
        str(APP_FILE),
        run_name="__main__",
    )


def main():
    updated, previous_state = (
        check_code_update()
    )

    try:
        run_app()

    except Exception:
        # 只在本次刚替换过 app.py 时回滚，
        # 避免一个坏版本把果子彻底卡死。
        if (
            updated
            and restore_backup(
                previous_state
            )
        ):
            print(
                "新版程序启动失败，"
                "已自动恢复上一版。"
            )
            run_app()
            return

        raise


if __name__ == "__main__":
    main()
