"""Navigation primitives that sit on top of the BLDC drive layer.

  * `obstacle_field.ObstacleField` - fuses lidar / ultrasonic / IR /
    depth into per-sector minimum distances so reactive controllers
    can answer "is direction X clear?" with one number.

  * `autonomous_pilot.AutonomousPilot` - the actual reactive wander
    behaviour. Reads the obstacle field, issues continuous wheel
    commands to a `DriveController`, and respects a hard emergency-stop
    layer.
"""
