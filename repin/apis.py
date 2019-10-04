import gitlab

from .config import config


class Api:
    _api = None

    def init(self):
        # TODO: choose api type from profile
        self._api = gitlab.Gitlab.from_config(config.current_profile(), [
            config.path
        ])

    def get(self):
        if not self._api:
            self.init()

        return self._api


api = Api()


def get():
    return api.get()
