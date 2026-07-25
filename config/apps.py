from simpleui.apps import SimpleApp


class SecureSimpleUIConfig(SimpleApp):
    """Keep SimpleUI without letting it remove clickjacking protection."""

    def ready(self):
        # simpleui's default ready() removes XFrameOptionsMiddleware. The admin
        # in this project is not intended to be embedded, so retain DENY.
        pass
