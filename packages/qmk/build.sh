#!/bin/sh
set -e

python3 -m pip install --user qmk
# qmk installed to ~/.local/bin which isn't on the path
# ln ~/.local/bin/qmk ~/bin/qmk
echo "$HOME/.local/bin" >> ~/.configs/profile.d/path/42-qmk

~/.local/bin/qmk setup -H ~/w/qmk_firmware -y

# sudo cp ~/w/qmk_firmware/util/udev/50-qmk.rules /etc/udev/rules.d/

tar -c -C ~ --verbatim-files-from --files-from ~/w/provides.txt -f ~/provides.tar
