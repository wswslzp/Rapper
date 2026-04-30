#!/bin/bash

echo "Test script is running"
echo "First argument is: ${1:-}"

case "${1:-}" in
    --daemon)
        echo "Found --daemon argument"
        ;;
    *)
        echo "No match found"
        ;;
esac