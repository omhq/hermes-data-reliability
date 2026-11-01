def _names(value: object, allowed: set[str]) -> tuple[str, ...]:
    if type(value) is not list or len(value) > 50:
        raise ValueError("Invalid tool package selection")

    selected = []

    for name in value:
        if type(name) is not str or name not in allowed or name in selected:
            raise ValueError("Invalid tool package selection")

        selected.append(name)

    return tuple(selected)


def select_toolsets(
    configuration: object, manifest: object
) -> tuple[str, ...]:
    """Select only toolsets in the host's installed package manifest."""
    if (
        type(configuration) is not dict
        or type(configuration.get("plugins")) is not dict
        or type(manifest) is not dict
        or type(manifest.get("version")) is not int
        or manifest["version"] != 1
        or type(manifest.get("packages")) is not dict
    ):
        raise ValueError("Invalid tool package selection")

    plugins = configuration["plugins"]
    packages = manifest["packages"]
    enabled = _names(plugins.get("enabled", []), set(packages))
    disabled = _names(plugins.get("disabled", []), set(packages))
    entries = plugins.get("entries", {})

    if type(entries) is not dict or not entries.keys() <= packages.keys():
        raise ValueError("Invalid tool package entries")

    toolsets = []

    for name in enabled:
        if name in disabled:
            continue

        package = packages[name]

        if (
            type(package) is not dict
            or package.get("enabled") is not True
            or type(package.get("toolset")) is not str
            or not package["toolset"]
            or type(package.get("options")) is not dict
            or entries.get(name, {}) != package["options"]
        ):
            raise ValueError("Tool package selection differs from host policy")

        toolsets.append(package["toolset"])

    return tuple(toolsets)
