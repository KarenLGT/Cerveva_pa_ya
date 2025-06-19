#!/usr/bin/env bash

# Salir si ocurre un error
set -o errexit

# Instala las dependencias del proyecto usando Poetry
poetry install --no-root
