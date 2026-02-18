@ECHO OFF
REM Deployment script (.cmd variant)
SET SERVER=10.0.0.1
ECHO Deploying to %SERVER%...
XCOPY /E /Y dist\ \\%SERVER%\share\
ECHO Deployment done.
