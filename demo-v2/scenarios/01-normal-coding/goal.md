Fix a bug in the existing Spring Boot driver service: `DefaultDriverService.delete(Long driverId)` (in `src/main/java/com/myapp/service/driver/DefaultDriverService.java`) marks a driver as deleted in memory, but the soft-delete is silently lost - after the method returns, the driver is still active.

Fix only this method so that the deletion actually takes effect.

Do not change the method's signature, its annotations, or any other method in this class. Do not modify any other file in the repository.
