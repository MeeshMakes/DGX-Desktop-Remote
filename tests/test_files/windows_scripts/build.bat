@ECHO OFF
REM Simple build script — should be converted to build.sh
SET PROJECT=DGX-Remote
SET BUILD_DIR=build\output

ECHO Building %PROJECT%...
MKDIR %BUILD_DIR%
COPY src\*.c %BUILD_DIR%\
ECHO Done.
