# SearXNG for the Zenith discovery layer.
#
# The stock image ships with the JSON API DISABLED, and a hosted image deploy
# can't mount a local settings file — so we bake ours in. Without this the
# engine's /search?format=json calls return 403 and discovery silently finds
# nothing, which looks identical to "there was no data".
FROM searxng/searxng:latest

COPY settings.yml /etc/searxng/settings.yml

ENV SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml
EXPOSE 8080
