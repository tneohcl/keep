#!/bin/sh

exec flatpak-spawn --host --directory=/ --forward-fd=1 --forward-fd=2 umount "$@"
