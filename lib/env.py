def load(path='.env'):
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                key, _, value = line.partition('=')
                env[key.strip()] = value.strip()
    except OSError:
        raise OSError("Could not open " + path)
    return env
