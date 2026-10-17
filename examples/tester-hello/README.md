# Hello Luma

A native LumaUI example for a running, qualified tester guest. From the repository:

```sh
luma-tester dev /path/to/mobile.json ./examples/tester-hello -- python3 main.py
```

Edit `message.txt` in your host checkout. The watcher syncs the source and restarts
the guest app. The same source runs in a desktop guest. No dependency installation
or build step is needed when the image includes LumaUI and Python.
