def parse_config(config, key):
    value = config.get(key)
    return value.upper()


def get_user_name(users, uid):
    user = users.get(uid)
    return user['name']
