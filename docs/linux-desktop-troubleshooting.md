# Linux troubleshooting and clean reinstall

The main README contains only **Install** and **Update**. These notes are for exceptional problems.

## Browser-less Linux desktops / remote servers

A GPU is not needed for WASA-DEM's Linux desktop shortcut. However, a local web browser is needed to display JupyterLab on the Linux desktop. On remote servers or thin-client XFCE sessions with no Firefox/Chromium, Jupyter may start correctly without opening a browser. Use `jupyter server list` to inspect running server URLs (tokens are private), then use a browser on your own computer through a secure SSH tunnel or supported network route.

If Emacs opens a temporary Jupyter HTML redirect instead of the browser, refresh the shortcut by running the **Update** command in the README. The launcher disables Jupyter's redirect-file mechanism and prefers Firefox/Chromium where available. XFCE may require right-click → Allow Launching. If the shortcut still does not open, run `bash ~/.local/bin/wasa-dem-launch` to read the actual Jupyter error. A timeout does not prove an ASP processing failure.

## Safe repair without deleting scientific environments

Re-run the **Install** command in the README. It reuses an existing working Python environment, checks ASP, and preserves an existing notebook workspace by default. An update refreshes the package and shortcut without re-installing ASP.

**Notebook refresh is optional** and may replace package-managed notebooks. Back up modified notebooks first, then run `bash update_linux.sh --refresh-notebooks`. The installer also accepts `WASA_DEM_REFRESH_NOTEBOOKS=1` as an explicit opt-in.

## Clean WASA-DEM-only environment reinstall (advanced)

Do not delete the entire Miniconda base installation or unrelated environments such as `asp`, `gdal`, `isis`, `jupyter`, `pleiades_asp_v2`, or Micromamba research environments. Do not delete a workspace that contains satellite imagery, DSMs, outputs, or edited notebooks. Back these up before any removal.

If `pleiades_asp` is exclusively used for WASA-DEM, stop WASA-DEM Jupyter servers, deactivate the environment, and remove only that environment:

```bash
conda deactivate
conda env remove -n pleiades_asp -y --solver classic
```

If the local Conda installation cannot complete the removal (for example due to a libmamba solver incompatibility), investigate the Conda configuration separately rather than forcibly deleting Miniconda. Then follow the standard **Install** instructions from the README.

A clean Linux VM is preferable when testing all prerequisites on a genuinely new computer.

## No curl available

Both the main README and installer support `wget` as the alternative downloader. If neither `curl` nor `wget` exists, a Linux system needs another means of fetching the installer (for example, a package manager or copying the installer file); neither script can download itself without a network downloader.
