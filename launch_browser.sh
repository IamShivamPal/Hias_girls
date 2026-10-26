#!/bin/bash

# Wait for the frontend service to be ready (port 5173)
while ! nc -z localhost 5173; do   
  sleep 1
done

# Launch Firefox in a new window to the HIAS dashboard
# Firefox usually remembers its last window state (maximized)
firefox --new-window http://localhost:5173 &
