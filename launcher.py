import hashlib
import json
import os
import re
import runpy
import shutil
import sys
import time
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
NETWORK_DOWNLOAD_ROUNDS = 3
NETWORK_RETRY_DELAYS = (0.0, 0.55, 1.20)

# 未来如果 app.py 需要新的启动器能力，
# 先在线更新 launcher.py，再提高 manifest 里的 app_api。
SUPPORTED_APP_API = 1
LAUNCHER_BUILD_VERSION = 33


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


def cache_busted_urls(urls):
    """给版本清单网址加毫秒时间戳，尽量绕开中间缓存。"""

    stamp = int(time.time() * 1000)

    result = []

    for index, url in enumerate(urls):
        separator = "&" if "?" in url else "?"
        result.append(
            f"{url}{separator}guozi_cb={stamp + index}"
        )

    return tuple(result)


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

    for round_index in range(
        NETWORK_DOWNLOAD_ROUNDS
    ):
        delay = NETWORK_RETRY_DELAYS[
            min(
                round_index,
                len(NETWORK_RETRY_DELAYS) - 1,
            )
        ]

        if delay > 0:
            time.sleep(delay)

        ordered_urls = list(
            cache_busted_urls(urls)
        )

        if round_index % 2 == 1:
            ordered_urls.reverse()

        for url in ordered_urls:
            try:
                return download_bytes(
                    url,
                    max_bytes,
                )
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
    """多轮逐线路下载，只有 hash + 内容检查都通过才接受。"""

    last_error = None
    expected = expected_sha256.lower().strip()

    if len(expected) != 64:
        raise ValueError("SHA256 格式错误。")

    for round_index in range(
        NETWORK_DOWNLOAD_ROUNDS
    ):
        delay = NETWORK_RETRY_DELAYS[
            min(
                round_index,
                len(NETWORK_RETRY_DELAYS) - 1,
            )
        ]

        if delay > 0:
            time.sleep(delay)

        ordered_urls = list(
            cache_busted_urls(urls)
        )

        if round_index % 2 == 1:
            ordered_urls.reverse()

        for url in ordered_urls:
            try:
                data = download_bytes(
                    url,
                    max_bytes,
                )

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


def detect_local_app_version():
    """从当前 app.py 源码里读取 APP_BUILD_VERSION；失败返回 0。"""

    if not APP_FILE.exists():
        return 0

    try:
        text = APP_FILE.read_text(
            encoding="utf-8"
        )
    except (
        OSError,
        UnicodeDecodeError,
    ):
        return 0

    match = re.search(
        r"(?m)^APP_BUILD_VERSION\s*=\s*(\d+)\s*$",
        text,
    )

    if match is None:
        return 0

    try:
        return int(match.group(1))
    except ValueError:
        return 0


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


def parse_version_manifest(data):
    """解析并做 launcher 需要的最小安全检查。"""

    try:
        version_data = json.loads(
            data.decode("utf-8")
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise ValueError(
            "version.json 无法解析。"
        ) from error

    if not isinstance(version_data, dict):
        raise ValueError(
            "version.json 格式错误。"
        )

    remote_version = int(
        version_data.get(
            "app_version",
            0,
        )
    )
    launcher_version = int(
        version_data.get(
            "launcher_version",
            0,
        )
    )
    required_api = int(
        version_data.get(
            "app_api",
            1,
        )
    )

    expected_hash = str(
        version_data["app_sha256"]
    ).lower().strip()

    if len(expected_hash) != 64:
        raise ValueError(
            "app SHA256 格式错误。"
        )

    # 同时验证 app_url 仍然只指向受信任仓库的 app.py。
    trusted_app_urls(
        version_data["app_url"]
    )

    return (
        version_data,
        remote_version,
        launcher_version,
    )


def download_best_version_manifest():
    """同时尝试 Raw / CDN，多轮收集后选择 app_version 最高的一份。"""

    candidates = []
    last_error = None

    for round_index in range(
        NETWORK_DOWNLOAD_ROUNDS
    ):
        delay = NETWORK_RETRY_DELAYS[
            min(
                round_index,
                len(NETWORK_RETRY_DELAYS) - 1,
            )
        ]

        if delay > 0:
            time.sleep(delay)

        urls = list(
            cache_busted_urls(
                VERSION_URLS
            )
        )

        # 轮流先碰 Raw / CDN，但不会“第一条成功就立刻相信”。
        if round_index % 2 == 1:
            urls.reverse()

        for source_index, url in enumerate(urls):
            try:
                data = download_bytes(
                    url,
                    MAX_VERSION_BYTES,
                )

                (
                    version_data,
                    remote_version,
                    launcher_version,
                ) = parse_version_manifest(
                    data
                )

                candidates.append(
                    (
                        remote_version,
                        launcher_version,
                        version_data,
                    )
                )

            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                urllib.error.URLError,
                urllib.error.HTTPError,
            ) as error:
                last_error = error

    if not candidates:
        if last_error is not None:
            raise last_error

        raise urllib.error.URLError(
            "无法获得可用的 version.json。"
        )

    # app 版本优先；若 app_version 一样，则 launcher_version 更高者优先。
    candidates.sort(
        key=lambda item: (
            item[0],
            item[1],
        ),
        reverse=True,
    )

    return candidates[0][2]


def check_code_update():
    """校验并更新 app.py；选择最新 manifest，并绝不自动降级。"""

    previous_state = read_state()
    state_version = int(
        previous_state.get(
            "app_version",
            0,
        )
    )
    detected_version = (
        detect_local_app_version()
    )
    local_version = max(
        state_version,
        detected_version,
    )

    try:
        version_data = (
            download_best_version_manifest()
        )

        remote_version = int(
            version_data.get(
                "app_version",
                0,
            )
        )

        required_api = int(
            version_data.get(
                "app_api",
                1,
            )
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

        local_hash = current_file_hash(
            APP_FILE
        )

        # 关键保护：任何旧缓存 / 旧 CDN manifest 都不能把
        # 已经更高版本的 app.py 自动降级。
        if remote_version < local_version:
            print(
                "检测到较旧的线上版本，"
                "保留本地 app：",
                f"local={local_version}, "
                f"remote={remote_version}",
            )

            # state 如果只是落后于 app.py 自身版本，顺手纠正。
            if (
                detected_version > state_version
                and local_hash is not None
            ):
                write_state(
                    detected_version,
                    local_hash,
                )

            return (
                False,
                previous_state,
            )

        # 同版本：文件 hash 正确就不下载。
        if (
            APP_FILE.exists()
            and remote_version == local_version
            and local_hash == expected_hash
        ):
            if (
                state_version != remote_version
                or previous_state.get(
                    "app_sha256"
                )
                != expected_hash
            ):
                write_state(
                    remote_version,
                    expected_hash,
                )

            return (
                False,
                previous_state,
            )

        # remote_version > local_version：
        # 正常升级。
        #
        # remote_version == local_version 但 hash 不同：
        # 视为本地 app.py 被改坏/写坏，重新下载同版本修复。
        app_bytes = (
            download_verified_from_sources(
                app_urls,
                MAX_APP_BYTES,
                expected_hash,
                validate_app_bytes,
            )
        )

        APP_NEW_FILE.write_bytes(
            app_bytes
        )

        written_bytes = (
            APP_NEW_FILE.read_bytes()
        )

        if (
            sha256_bytes(written_bytes)
            != expected_hash
        ):
            raise ValueError(
                "app.py 写入磁盘后校验失败。"
            )

        validate_app_bytes(
            written_bytes
        )

        if APP_FILE.exists():
            shutil.copy2(
                APP_FILE,
                APP_BACKUP_FILE,
            )

        APP_NEW_FILE.replace(
            APP_FILE
        )

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
    print(
        f"Guozi launcher build {LAUNCHER_BUILD_VERSION}"
    )

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
