#!/usr/bin/env python3

from pathlib import Path
import argparse
import json
import os
import random
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

HOSTNAME = socket.gethostname()
HOME = Path.home()
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache"))
SCRIPT_NAME = os.path.basename(sys.argv[0])
SCRIPT_PATH = Path(os.path.dirname(os.path.realpath(__file__)))
WORK_DIRS = SCRIPT_PATH / "work"
HOME_DIRS = CACHE_DIR / "boxes" / "home"

SEEDS = {
    "configs": {
        "directory": HOME,
        "include": [
            ".bashrc",
            ".gdbinit",
            ".gitconfig",
            ".gitignore",
            ".ipython",
            ".npmrc",
            ".profile",
            ".sqliterc",
            ".vimrc",
            ".zshenv",
            ".zshrc",
            "configs",
        ],
    },
    "firefox": {
        "include": [
            ".dev-init/firefox.sh",
            ".mozilla/firefox",
            "bin/firefox",
            "opt/firefox",
        ],
        "update_script": SCRIPT_PATH / "firefox-update.sh",
    },
    "go": {
        "include": ["go"],
        "update_script": SCRIPT_PATH / "go-update.sh",
    },
    "mold": {
        "include": [
            ".cargo/config.toml",
            "bin/mold",
        ],
        "update_script": SCRIPT_PATH / "mold-update.sh",
    },
    "node": {
        "depends": ["configs"],
        "include": [
            ".cache/node-versions.json",
            ".npm",
            "opt/node",
        ],
        "update_script": SCRIPT_PATH / "node-update.sh",
    },
    "python": {
        "include": [
            ".pylama.ini",
            "opt/python",
        ],
        "update_script": SCRIPT_PATH / "python-update.sh",
    },
    "rust": {
        "depends": ["mold"],
        "include": [
            ".cargo",
            ".local/share/bash-completion/completions",
            ".rustup",
            ".zfunc",
        ],
        "update_script": SCRIPT_PATH / "rust-update.sh",
    },
    "vscodium": {
        "include": [
            ".dev-init/vscodium.sh",
            ".vscode-oss",
            "bin/codium",
            "opt/vscodium",
        ],
        "update_script": SCRIPT_PATH / "vscodium-update.sh",
    },
}
DEFAULT_SEEDS = SEEDS.keys()


def rmtree(path):
    try:
        shutil.rmtree(path)
    except PermissionError:
        # This is needed to handle read-only directories, such as Go's packages.
        # See <https://github.com/golang/go/issues/27161>.
        subprocess.run(["chmod", "-R", "u+rwX", path], check=True)
        shutil.rmtree(path)


def update_seeds():
    now = time.time()
    done = set()
    todo = SEEDS.keys()
    while len(todo) > 0:
        later = []
        for key in todo:
            seed = SEEDS[key]
            if done.issuperset(seed.get("depends", [])):
                update_seed(key, now)
                done.add(key)
            else:
                later.append(key)
        if len(later) == len(todo):
            raise RuntimeError(f"Seed dependencies are unsatisfiable for: {list(todo)}")
        todo = later


def update_seed(key, now):
    seed = SEEDS[key]
    name = f"seed-{key}"
    try:
        update_script = seed["update_script"]
    except KeyError:
        return
    work_dir = WORK_DIRS / name
    if not work_dir.exists():
        work_dir.mkdir(parents=True)
    try:
        updated = (HOME_DIRS / name / ".UPDATED").stat().st_mtime
    except FileNotFoundError:
        updated = 0
    if update_script.stat().st_mtime < updated and now - updated < 60 * 60 * 12:
        return
    print(f"Updating {key} seed")
    run(name, seeds=seed.get("depends", []), init=(SCRIPT_PATH / "dev-init.sh"))
    run(name, init=update_script)


def enter_environment(name):
    if not (WORK_DIRS / name).exists():
        print(f"error: environment {name!s} does not exist")
        sys.exit(1)
    run(name)


def exec_environment(name, command, args):
    if not (WORK_DIRS / name).exists():
        print(f"error: environment {name!s} does not exist")
        sys.exit(1)
    run(name, exec=([command] + args))


def du(path):
    result = subprocess.run(
        ["du", "-cs", "--block-size=1", "--time", "--time-style=+%s", path],
        capture_output=True,
        check=False,  # ignore permissions errors
        encoding="utf-8",
    )
    m = re.search(
        r"^(?P<size>[^\t]+)\t(?P<mtime>[0-9]+)\ttotal$",
        result.stdout,
        re.MULTILINE,
    )
    if m is None:
        raise RuntimeError("Unexpected output from du")
    size = int(m.group("size"))
    mtime = int(m.group("mtime"))
    return (result.stderr != "", size, mtime)


def try_iterdir(path):
    try:
        yield from path.iterdir()
    except FileNotFoundError:
        pass


def rel_time(duration):
    duration /= 60
    if duration < 59.5:
        return f"{duration:.0f} minutes"
    duration /= 60
    if duration < 23.5:
        return f"{duration:.0f} hours"
    duration /= 24
    return f"{duration:.0f} days"


def si_bytes(size):
    if size < 1_000:
        return f"{size} B"
    if size < 999_950:
        return f"{size/1e3:.1f} kB"
    if size < 999_950 * 1e3:
        return f"{size/1e6:.1f} MB"
    if size < 999_950 * 1e6:
        return f"{size/1e9:.1f} GB"
    return f"{size/1e12:.1f} TB"


def list_environments(format="default"):
    envs = {}
    now = time.time()

    if format == "names":
        # fast path for shell completions
        for name in sorted([dir.name for dir in try_iterdir(WORK_DIRS)]):
            print(name)
        return

    for dir in try_iterdir(WORK_DIRS):
        env = envs.setdefault(dir.name, {})
        env["work_dir"] = str(dir)
        try:
            (
                env["work_dir_du_error"],
                env["work_dir_size"],
                env["work_dir_mtime"],
            ) = du(dir)
        except RuntimeError:
            pass

    for dir in try_iterdir(HOME_DIRS):
        env = envs.setdefault(dir.name, {})
        env["home_dir"] = str(dir)
        try:
            (
                env["home_dir_du_error"],
                env["home_dir_size"],
                env["home_dir_mtime"],
            ) = du(dir)
        except RuntimeError:
            pass

    if format == "json":
        print(json.dumps(envs, sort_keys=True, indent=4))
    else:
        nw = max([10] + [len(name) for name in envs])
        print(
            "{:<{nw}} | {:^21} | {:^21}".format(
                "",
                "home directory",
                "work directory",
                nw=nw,
            )
        )
        print(
            "{:<{nw}} | {:>10} {:>10} | {:>10} {:>10}".format(
                "name",
                "size",
                "modified",
                "size",
                "modified",
                nw=nw,
            )
        )
        print("{0:-<{nw}} + {0:-<10} {0:-<10} + {0:-<10} {0:-<10}".format("", nw=nw))
        for name in sorted(envs):
            env = envs[name]
            if "home_dir_size" in env:
                home_dir_size = si_bytes(env["home_dir_size"])
                if env["home_dir_du_error"]:
                    home_dir_size += "+"
            else:
                home_dir_size = "N/A"
            if "work_dir_size" in env:
                work_dir_size = si_bytes(env["work_dir_size"])
                if env["work_dir_du_error"]:
                    work_dir_size += "+"
            else:
                work_dir_size = "N/A"
            print(
                "{:<{nw}} | {:>10} {:>10} | {:>10} {:>10}".format(
                    name,
                    home_dir_size,
                    rel_time(now - env["home_dir_mtime"])
                    if "home_dir_mtime" in env
                    else "N/A",
                    work_dir_size,
                    rel_time(now - env["work_dir_mtime"])
                    if "work_dir_mtime" in env
                    else "N/A",
                    nw=nw,
                )
            )


def new_environment(name, seeds=DEFAULT_SEEDS):
    work_dir = WORK_DIRS / name
    if work_dir.exists() or (HOME_DIRS / name).exists():
        print(
            f"error: environment {name!s} exists (did you mean '{SCRIPT_NAME} reset'?)"
        )
        sys.exit(1)
    update_seeds()
    work_dir.mkdir(parents=True)
    run(name, seeds=seeds, init=(SCRIPT_PATH / "dev-init.sh"))


def random_names():
    # 1. Prefer the EFF short word list. See https://www.eff.org/dice for more
    # info.
    words = None
    try:
        words = open(CACHE_DIR / "eff_short_wordlist_1.txt").readlines()
    except FileNotFoundError:
        url = "https://www.eff.org/files/2016/09/08/eff_short_wordlist_1.txt"
        try:
            words = urllib.request.urlopen(url).read().decode("utf-8")
        except (urllib.request.HTTPError, urllib.request.URLError) as e:
            print(f"Warning: failed to download EFF short wordlist from {url}: {e}")
        else:
            open(CACHE_DIR / "eff_short_wordlist_1.txt", "w").write(words)
            words = words.split("\n")
    if words is not None:
        for _ in range(200):
            word = random.choice(words).split()[1]
            if len(word) <= 10 and word.islower() and word.isalpha():
                yield word

    # 2. /usr/share/dict/words
    try:
        words = open("/usr/share/dict/words").readlines()
    except FileNotFoundError:
        pass
    else:
        for _ in range(200):
            word = random.choice(words).strip()
            if len(word) <= 6 and word.islower() and word.isalpha():
                yield word

    # 3. Random 6 letters
    for _ in range(20):
        yield "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=6))

    # 4. Random 32 letters
    yield "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=32))


def create_enter_tmp_environment(seeds=DEFAULT_SEEDS):
    for name in random_names():
        name = f"tmp-{name}"
        if not (WORK_DIRS / name).exists() and not (HOME_DIRS / name).exists():
            update_seeds()
            (WORK_DIRS / name).mkdir(parents=True)
            run(name, seeds=seeds, init=(SCRIPT_PATH / "dev-init.sh"))
            run(name)
            return
    raise RuntimeError("failed to generate random environment name")


def purge_environment(name):
    host_work = WORK_DIRS / name
    host_home = HOME_DIRS / name
    if not host_work.exists() and not host_home.exists():
        print(f"warning: environment {name} does not exist (nothing to purge)")
        return
    if host_work.exists():
        rmtree(host_work)
    if host_home.exists():
        rmtree(host_home)


def reset_environment(name, seeds=DEFAULT_SEEDS, clean=False):
    if name.startswith("seed-"):
        print(
            f"Resetting a seed environment is unlikely to work as expected. Consider '{SCRIPT_NAME} purge' instead. Aborting"
        )
        sys.exit(1)
    if not (WORK_DIRS / name).exists():
        print(
            f"error: environment {name!s} does not exist (did you mean '{SCRIPT_NAME} new'?)"
        )
        sys.exit(1)
    host_home = HOME_DIRS / name
    if host_home.exists():
        rmtree(host_home)
    if not args.clean:
        update_seeds()
        run(name, seeds=seeds, init=(SCRIPT_PATH / "dev-init.sh"))


def flatten(*l):
    def gen(l):
        for x in l:
            if isinstance(x, (list, tuple)):
                yield from gen(x)
            else:
                yield x

    return list(gen(l))


def ro_bind_try(a, b=None):
    if b is None:
        return ("--ro-bind-try", a, a)
    else:
        return ("--ro-bind-try", a, b)


def run(name, seeds=[], init=False, exec=False):
    # print(f'run({name}, seeds={seeds}, init={init}, exec={exec}')
    host_home = HOME_DIRS / name
    host_work = WORK_DIRS / name

    try:
        host_home.mkdir(parents=True)
    except FileExistsError:
        pass

    seed = None
    if len(seeds) > 0:
        args = ["tar", "-c"]
        for seed in seeds:
            spec = SEEDS[seed]
            if "directory" in spec:
                args.extend(["--directory", spec["directory"]])
            else:
                args.extend(["--directory", HOME_DIRS / f"seed-{seed}"])
            args.extend(spec["include"])
        seed = subprocess.Popen(args, stdout=subprocess.PIPE)

    seccomp = open(SCRIPT_PATH / "podman.bpf")
    bwrap = subprocess.Popen(
        flatten(
            "bwrap",
            "--die-with-parent",
            "--unshare-cgroup",
            "--unshare-ipc",
            "--unshare-pid",
            "--unshare-uts",
            ("--hostname", f"{name}.{HOSTNAME}"),
            ("--symlink", "/usr/bin", "/bin"),
            ("--dev", "/dev"),
            (ro_bind_try(init, "/dev/shm/init.sh") if init else []),
            (
                []
                if seed is None
                else ["--file", str(seed.stdout.fileno()), "/dev/shm/seed.tar"]
            ),
            ro_bind_try("/etc"),
            ("--bind", host_home, HOME),
            ("--dir", HOME / ".dev-init"),
            ("--dir", HOME / "bin"),
            ("--dir", HOME / "opt"),
            ("--dir", HOME / "tmp"),
            ("--bind", host_work, HOME / name),
            ("--symlink", "/usr/lib", "/lib"),
            ("--symlink", "/usr/lib64", "/lib64"),
            ro_bind_try("/opt"),
            ("--proc", "/proc"),
            ("--symlink", "/usr/sbin", "/sbin"),
            ("--tmpfs", "/tmp"),
            ro_bind_try("/usr"),
            ro_bind_try("/var/lib/apt/lists/"),
            ro_bind_try("/var/lib/dpkg/"),
            ("--seccomp", str(seccomp.fileno())),
            ("--chdir", HOME / name),
            "--",
            (
                [os.environ["SHELL"], "-c", "/dev/shm/init.sh"]
                if init
                else [os.environ["SHELL"], "-c", shlex.join(exec)]
                if exec
                else os.environ["SHELL"]
            ),
        ),
        env={
            "DISPLAY": os.environ["DISPLAY"],
            "HOME": os.environ["HOME"],
            "PATH": f"{HOME}/bin:/bin:/sbin",
            "SANDBOX": name,
            "SHELL": os.environ["SHELL"],
            "TERM": os.environ["TERM"],
            "TMPDIR": HOME / "tmp",
        },
        pass_fds=[
            *([] if seed is None else [seed.stdout.fileno()]),
            seccomp.fileno(),
        ],
    )

    if seed is not None:
        seed.stdout.close()  # so tar receives SIGPIPE

    if bwrap.wait() != 0:
        raise subprocess.CalledProcessError(bwrap.returncode, "bwrap")

    if seed is not None:
        seed.wait()


def seed_list(seeds):
    if seeds in ["", "none"]:
        return []
    if seeds == "default":
        return DEFAULT_SEEDS
    if seeds == "all":
        return SEEDS.keys()
    seeds = seeds.split(",")
    for seed in seeds:
        if seed not in SEEDS:
            options = ", ".join([repr(s) for s in SEEDS])
            raise argparse.ArgumentTypeError(
                f"invalid seed {seed!r} (use 'none', 'default', or 'all', "
                f"or comma-separated list from {options})"
            )
    return seeds


def parse_args():
    parser = argparse.ArgumentParser(
        description="Manage sandboxed environments", allow_abbrev=False
    )
    subparsers = parser.add_subparsers(title="commands", dest="command", metavar=None)

    parser_enter = subparsers.add_parser(
        "enter", help="Run a shell in an existing environment", allow_abbrev=False
    )
    parser_enter.add_argument("name", help="Environment name")

    parser_exec = subparsers.add_parser(
        "exec", help="Run a command in an existing environment", allow_abbrev=False
    )
    parser_exec.add_argument("name", help="Environment name")
    parser_exec.add_argument("exec", metavar="command", help="Command to run")
    parser_exec.add_argument(
        "args",
        nargs="*",
        help='Arguments to command (use "--" before command to disambiguate)',
    )

    parser_list = subparsers.add_parser(
        "list", help="Show existing environments", allow_abbrev=False
    )
    parser_list.add_argument(
        "--format", choices=["default", "json", "names"], help="Set output format"
    )

    parser_new = subparsers.add_parser(
        "new", help="Create a new environment", allow_abbrev=False
    )
    parser_new.add_argument(
        "--enter", action="store_true", help="Run a shell in new environment"
    )
    parser_new.add_argument(
        "--seeds",
        type=seed_list,
        default="default",
        help="Set(s) of files to inject into home directory, comma-separated",
    )
    parser_new.add_argument("name", help="Environment name")

    parser_purge = subparsers.add_parser(
        "purge", help="Delete an environment and its work directory", allow_abbrev=False
    )
    parser_purge.add_argument("name", nargs="+", help="Environment name(s)")

    parser_reset = subparsers.add_parser(
        "reset",
        help="Recreate an environment (keeping its work directory)",
        allow_abbrev=False,
    )
    parser_reset.add_argument(
        "--clean",
        action="store_true",
        help="Remove home directory and do not recreate it",
    )
    parser_reset.add_argument(
        "--seeds",
        type=seed_list,
        default="default",
        help="Sets of files to inject into home directory, comma-separated",
    )
    parser_reset.add_argument("name", nargs="+", help="Environment name(s)")

    parser_tmp = subparsers.add_parser(
        "tmp", help="Create and enter a new temporary environment", allow_abbrev=False
    )
    parser_tmp.add_argument("--seeds", type=seed_list, default="default")

    # TODO: rename

    args = parser.parse_args()
    if args.command is None:
        parser.print_help(sys.stderr)
        sys.exit(1)
    return args


if __name__ == "__main__":
    args = parse_args()
    if args.command == "enter":
        enter_environment(name=args.name)
    elif args.command == "exec":
        exec_environment(name=args.name, command=args.exec, args=args.args)
    elif args.command == "list":
        list_environments(format=args.format)
    elif args.command == "new":
        new_environment(name=args.name, seeds=args.seeds)
        if args.enter:
            enter_environment(name=args.name)
    elif args.command == "purge":
        for name in args.name:
            purge_environment(name=name)
    elif args.command == "reset":
        for name in args.name:
            reset_environment(name=name, seeds=args.seeds, clean=args.clean)
    elif args.command == "tmp":
        create_enter_tmp_environment(seeds=args.seeds)
    else:
        raise RuntimeError(f"unknown command: {args}")
