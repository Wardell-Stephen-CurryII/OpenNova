def init_config(defaults={'host': 'localhost', 'port': 5432}):
    defaults['debug'] = False
    return defaults


def build_url(protocol, host, path_parts=[]):
    path_parts.insert(0, host)
    path_parts.insert(0, protocol)
    return '/'.join(path_parts)
