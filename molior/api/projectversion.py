from aiohttp import web

from molior.app import app, logger
from molior.auth import req_role
from molior.model.projectversion import ProjectVersion, get_projectversion_deps
from molior.model.project import Project
from molior.model.sourcerepository import SourceRepository
from molior.model.sourepprover import SouRepProVer
from molior.tools import ErrorResponse, parse_int, is_name_valid


def get_projectversion_deps_manually(projectversion, to_dict=True):
    """
    Returns all dependencies of given projectversion (recursive).

    Args:
        projectversion (ProjectVersion): The ProjectVersion model instance.
        to_dict (bool): If True output will be dict.
    """

    deps = []

    def get_deps(projectv):
        """
        Returns a list of dependencies
        of the given projectversion.
        Recursively calls this function until
        all subdependencies are appended to
        the "deps" list.

        Args:
            projectv (ProjectVersion): The ProjectVersion model instance.
        """
        if not projectv:
            return

        if projectv not in deps and projectversion.id != projectv.id:
            dep = projectversion_to_dict(projectv) if to_dict else projectv
            if dep not in deps:
                deps.append(dep)

        for dep in projectv.dependencies:
            get_deps(dep)

    get_deps(projectversion)

    return deps


def projectversion_to_dict(projectversion):
    """
    Returns the given projectversion object
    as dist, which can be processed by
    json_response
    ---
    Args:
        projectversion (object): The projectversion from the database
            provided by SQLAlchemy.
    Returns:
        dict: The dict which can be processed by json_response

    """
    return {
        "id": projectversion.id,
        "name": projectversion.name,
        "project_name": projectversion.project.name,
        "apt_url": projectversion.get_apt_repo(url_only=True),
        "project": {
            "id": projectversion.project.id,
            "name": projectversion.project.name,
            "description": projectversion.project.description,
        },
        "basemirror": projectversion.basemirror.fullname,
        "architectures": projectversion.mirror_architectures[1:-1].split(","),
        "is_locked": projectversion.is_locked,
        "ci_builds_enabled": projectversion.ci_builds_enabled,
    }


@app.http_get("/api/projectversions")
@app.authenticated
async def get_projectversions(request):
    """
    Returns a list of projectversions.

    ---
    description: Returns a list of projectversions.
    tags:
        - ProjectVersions
    consumes:
        - application/x-www-form-urlencoded
    parameters:
        - name: exclude_id
          in: query
          required: false
          type: integer
        - name: basemirror_id
          in: query
          required: false
          type: integer
        - name: is_basemirror
          in: query
          required: false
          type: bool
        - name: project_id
          in: query
          required: false
          type: integer
        - name: project_name
          in: query
          required: false
          type: string
        - name: dependant_id
          in: query
          required: false
          type: integer
    produces:
        - text/json
    responses:
        "200":
            description: successful
        "500":
            description: internal server error
    """
    project_id = request.GET.getone("project_id", None)
    project_name = request.GET.getone("project_name", None)
    exclude_id = request.GET.getone("exclude_id", None)
    basemirror_id = request.GET.getone("basemirror_id", None)
    is_basemirror = request.GET.getone("isbasemirror", False)
    dependant_id = request.GET.getone("dependant_id", None)

    query = (
        request.cirrina.db_session.query(ProjectVersion)
        .join(Project)
        .filter(ProjectVersion.is_deleted == False)  # noqa: E712
    )

    exclude_id = parse_int(exclude_id)
    if exclude_id:
        query = query.filter(Project.id != exclude_id)

    project_id = parse_int(project_id)
    if project_id:
        query = query.filter(Project.id == project_id)

    if project_name:
        query = query.filter(Project.name == project_name)

    if basemirror_id:
        query = query.filter(ProjectVersion.base_mirror_id == basemirror_id)
    elif is_basemirror:
        query = query.filter(Project.is_basemirror.is_(True), ProjectVersion.mirror_state == "ready")

    if dependant_id:
        logger.info("dependant_id")
        p_version = request.cirrina.db_session.query(ProjectVersion).filter(ProjectVersion.id == dependant_id).first()
        projectversions = []
        if p_version:
            projectversions = [p_version.basemirror]
        nb_projectversions = len(projectversions)
    else:
        query = query.order_by(Project.name, ProjectVersion.name)
        projectversions = query.all()
        nb_projectversions = query.count()

    results = []

    for projectversion in projectversions:
        projectversion_dict = projectversion_to_dict(projectversion)
        projectversion_dict["dependencies"] = get_projectversion_deps_manually(projectversion)
        results.append(projectversion_dict)

    data = {"total_result_count": nb_projectversions, "results": results}

    return web.json_response(data)


@app.http_get("/api/projectversions/{projectversion_id}")
@app.authenticated
async def get_projectversion(request):
    """
    Returns the projectversion.

    ---
    description: Return the projectversion
    tags:
        - ProjectVersions
    consumes:
        - application/x-www-form-urlencoded
    parameters:
        - name: projectversion_id
          in: path
          required: true
          type: integer
    produces:
        - text/json
    responses:
        "200":
            description: successful
        "500":
            description: internal server error
    """
    projectversion_id = request.match_info["projectversion_id"]
    try:
        projectversion_id = int(projectversion_id)
    except (ValueError, TypeError):
        return ErrorResponse(400, "Incorrect value for projectversion_id")

    projectversion = (
        request.cirrina.db_session.query(ProjectVersion)
        .filter(ProjectVersion.id == projectversion_id)  # pylint: disable=no-member
        .first()
    )

    if not projectversion:
        return ErrorResponse(400, "Projectversion %d not found" % projectversion_id)

    if projectversion.is_deleted:
        return ErrorResponse(404, "Projectversion {} deleted".format(projectversion_id))

    projectversion_dict = projectversion_to_dict(projectversion)
    projectversion_dict["dependencies"] = get_projectversion_deps_manually(projectversion)

    projectversion_dict["basemirror_url"] = str()
    if projectversion.basemirror:
        projectversion_dict["basemirror_url"] = projectversion.basemirror.get_apt_repo()

    return web.json_response(projectversion_dict)


@app.http_post("/api/projects/{project_id}/versions")
@req_role("owner")
async def create_projectversions(request):
    """
    Creates a new projectversion.

    ---
    description: Creates a new projectversion.
    tags:
        - ProjectVersions
    consumes:
        - application/json
    parameters:
        - name: project
          in: path
          required: true
          type: string
        - name: body
          in: body
          required: true
          schema:
            type: object
            properties:
                name:
                    type: string
                    example: "1.0.0"
                basemirror:
                    type: string
                    example: "stretch/9.6"
                architectures:
                    type: array
                    example: ["amd64", "armhf"]
                    FIXME: only accept existing archs on mirror!
    produces:
        - text/json
    responses:
        "200":
            description: successful
        "400":
            description: invalid data received
        "500":
            description: internal server error
    """
    params = await request.json()

    name = params.get("name")
    architectures = params.get("architectures", [])
    basemirror = params.get("basemirror")
    project_id = request.match_info["project_id"]

    if not project_id:
        return ErrorResponse(400, "No valid project id received")
    if not name:
        return ErrorResponse(400, "No valid name for the projectversion recieived")
    if not basemirror or not ("/" in basemirror):
        return ErrorResponse(400, "No valid basemirror received (format: 'name/version')")
    if not architectures:
        return ErrorResponse(400, "No valid architecture received")

    if not is_name_valid(name):
        return ErrorResponse(400, "Invalid project name!")

    basemirror_name, basemirror_version = basemirror.split("/")

    # FIXME: verify valid architectures

    project = request.cirrina.db_session.query(Project).filter(Project.name == project_id).first()
    if not project:
        project = request.cirrina.db_session.query(Project).filter(Project.id == project_id).first()
        if not project:
            return ErrorResponse(400, "Project '{}' could not be found".format(project_id))

    projectversion = request.cirrina.db_session.query(ProjectVersion).filter(ProjectVersion.name == name,
                                                                             Project.id == project.id).first()
    if projectversion:
        return ErrorResponse(400, "Projectversion already exists. {}".format(
                "And is marked as deleted!" if projectversion.is_deleted else ""))

    basemirror = request.cirrina.db_session.query(ProjectVersion).filter(ProjectVersion.parent.name == basemirror_name,
                                                                         ProjectVersion.name == basemirror_version).first()
    if not basemirror:
        return ErrorResponse(400, "Base mirror not found: {}/{}".format(basemirror_name, basemirror_version))

    projectversion = ProjectVersion(name=name, project=project, architectures=architectures, basemirror=basemirror)
    request.cirrina.db_session.add(projectversion)
    request.cirrina.db_session.commit()

    logger.info("ProjectVersion '%s/%s' with id '%s' added",
                projectversion.project.name,
                projectversion.name,
                projectversion.id,
                )

    project_name = projectversion.project.name
    project_version = projectversion.name

    await request.cirrina.aptly_queue.put({"init_repository": [
                projectversion.id,
                basemirror_name,
                basemirror_version,
                project_name,
                project_version,
                architectures]})

    return web.json_response({"id": projectversion.id, "name": projectversion.name})


@app.http_post("/api/projectversions/{projectversion_id}/repositories/{sourcerepository_id}")
@req_role(["member", "owner"])
async def post_add_repository(request):
    """
    Adds given sourcerepositories to the given
    projectversion.

    ---
    description: Adds given sourcerepositories to given projectversion.
    tags:
        - ProjectVersions
    consumes:
        - application/json
    parameters:
        - name: projectversion_id
          in: path
          required: true
          type: integer
        - name: sourcerepository_id
          in: path
          required: true
          type: integer
        - name: body
          in: body
          required: true
          schema:
            type: object
            properties:
                buildvariants:
                    type: array
                    example: [1, 2]
    produces:
        - text/json
    responses:
        "200":
            description: successful
        "400":
            description: Invalid data received.
    """
    return ErrorResponse(400, "API obsolete")


@app.http_delete("/api/projectversions/{projectversion_id}/repositories/{sourcerepository_id}")
@req_role(["member", "owner"])
async def delete_repository(request):
    """
    Adds given sourcerepositories to the given
    projectversion.

    ---
    description: Adds given sourcerepositories to given projectversion.
    tags:
        - ProjectVersions
    consumes:
        - application/json
    parameters:
        - name: projectversion_id
          in: path
          required: true
          type: integer
        - name: sourcerepository_id
          in: path
          required: true
          type: integer
    produces:
        - text/json
    responses:
        "200":
            description: successful
        "400":
            description: Invalid data received.
    """
    projectversion_id = parse_int(request.match_info["projectversion_id"])
    sourcerepository_id = parse_int(request.match_info["sourcerepository_id"])
    projectversion_id = parse_int(projectversion_id)
    if not projectversion_id:
        return ErrorResponse(400, "No valid projectversion_id received")

    projectversion = (
        request.cirrina.db_session.query(ProjectVersion)  # pylint: disable=no-member
        .filter(ProjectVersion.id == projectversion_id)
        .first()
    )

    if not projectversion:
        return ErrorResponse(400, "Projectversion {} could not been found.".format(projectversion_id))

    if not sourcerepository_id:
        return ErrorResponse(400, "No valid sourcerepository_id received")

    sourcerepository = (
        request.cirrina.db_session.query(SourceRepository)
        .filter(SourceRepository.id == sourcerepository_id)
        .first()
    )

    if not sourcerepository:
        return ErrorResponse(400, "Sourcerepository {} could not been found".format(sourcerepository_id))

    # get the association of the projectversion and the sourcerepository
    sourcerepositoryprojectversion = (
        request.cirrina.db_session.query(SouRepProVer)  # pylint: disable=no-member
        .filter(SouRepProVer.c.sourcerepository_id == sourcerepository_id)
        .filter(SouRepProVer.c.projectversion_id == projectversion.id)
    ).first()
    if not sourcerepositoryprojectversion:
        return ErrorResponse(400, "Could not find the sourcerepository for the projectversion")

    projectversion.sourcerepositories.remove(sourcerepository)
    request.cirrina.db_session.commit()

    return web.Response(status=200, text="Sourcerepository removed from projectversion")


@app.http_post("/api/projectversions/{projectversion_id}/clone")
@req_role("owner")
async def clone_projectversion(request):
    """
    Clone a given projectversion

    ---
    description: Toggles the ci enabled flag on a projectversion.
    tags:
        - ProjectVersions
    consumes:
        - application/json
    parameters:
        - name: projectversion_id
          in: path
          required: true
          type: integer
        - name: body
          in: body
          required: true
          schema:
            type: object
            properties:
                name:
                    type: string
                    example: "1.0.0"
    produces:
        - text/json
    responses:
        "200":
            description: successful
        "400":
            description: Invalid data received.
        "500":
            description: internal server error
    """
    params = await request.json()

    name = params.get("name")
    projectversion_id = parse_int(request.match_info["projectversion_id"])

    if not projectversion_id:
        return ErrorResponse(400, "No valid project id received")
    if not name:
        return ErrorResponse(400, "No valid name for the projectversion recieived")
    if not is_name_valid(name):
        return ErrorResponse(400, "Invalid project name!")

    projectversion = request.cirrina.db_session.query(ProjectVersion).filter(ProjectVersion.id == projectversion_id).first()

    if request.cirrina.db_session.query(ProjectVersion).join(Project).filter(
            ProjectVersion.name == name,
            Project.id == projectversion.project_id).first():
        return ErrorResponse(400, "Projectversion already exists.")

    new_projectversion = ProjectVersion(
        name=name,
        project=projectversion.project,
        dependencies=projectversion.dependencies,
        mirror_architectures=projectversion.mirror_architectures,
        basemirror_id=projectversion.basemirror_id,
        sourcerepositories=projectversion.sourcerepositories,
        ci_builds_enabled=projectversion.ci_builds_enabled,
    )

    for repo in new_projectversion.sourcerepositories:
        sourepprover = request.cirrina.db_session.query(SouRepProVer).filter(
                SouRepProVer.c.sourcerepository_id == repo.id,
                SouRepProVer.c.projectversion_id == projectversion.id).first()
        new_sourepprover = request.cirrina.db_session.query(SouRepProVer).filter(
                SouRepProVer.c.sourcerepository_id == repo.id,
                SouRepProVer.c.projectversion_id == new_projectversion.id).first()
        new_sourepprover.architectures = sourepprover.architectures

    request.cirrina.db_session.add(new_projectversion)
    request.cirrina.db_session.commit()

    await request.cirrina.aptly_queue.put(
        {
            "init_repository": [
                new_projectversion.id,
                new_projectversion.basemirror.project.name,
                new_projectversion.basemirror.name,
                new_projectversion.project.name,
                new_projectversion.name,
                new_projectversion.architectures[1:-1].split(","),
            ]
        }
    )

    return web.json_response(
        {"id": new_projectversion.id, "name": new_projectversion.name}
    )


@app.http_post("/api/projectversions/{projectversion_id}/overlay")
@req_role("owner")
async def create_projectversion_overlay(request):
    """
    Creates an overlay of a project version

    ---
    description: Creates an overlay of a project version
    tags:
        - ProjectVersions
    consumes:
        - application/json
    parameters:
        - name: projectversion_id
          in: path
          required: true
          type: integer
        - name: body
          in: body
          required: true
          schema:
            type: object
            properties:
                name:
                    type: string
    produces:
        - text/json
    responses:
        "200":
            description: successful
        "400":
            description: Invalid data received.
        "500":
            description: internal server error
    """
    params = await request.json()

    name = params.get("name")
    projectversion_id = parse_int(request.match_info["projectversion_id"])

    if not projectversion_id:
        return ErrorResponse(400, "No valid project id received")
    if not name:
        return ErrorResponse(400, "No valid name for the projectversion recieived")
    if not is_name_valid(name):
        return ErrorResponse(400, "Invalid project name!")

    projectversion = (
        request.cirrina.db_session.query(ProjectVersion)
        .filter(ProjectVersion.id == projectversion_id)
        .first()
    )

    if (
        request.cirrina.db_session.query(ProjectVersion)
        .join(Project)
        .filter(ProjectVersion.name == name)
        .filter(Project.id == projectversion.project_id)
        .first()
    ):
        return ErrorResponse(400, "Projectversion already exists.")

    # remove association from database
    overlay_projectversion = ProjectVersion(
        name=name,
        project=projectversion.project,
        # add the projectversion where the overlay is created from as a dependency
        dependencies=[projectversion],
        architectures=projectversion.architectures,
        basemirror=projectversion.basemirror
    )

    request.cirrina.db_session.add(overlay_projectversion)
    request.cirrina.db_session.commit()

    basemirror = overlay_projectversion.basemirror
    architectures = overlay_projectversion.architectures[1:-1].split(",")

    await request.cirrina.aptly_queue.put(
        {
            "init_repository": [
                overlay_projectversion.id,
                basemirror.project.name,
                basemirror.name,
                overlay_projectversion.project.name,
                overlay_projectversion.name,
                architectures,
            ]
        }
    )

    return web.json_response(
        {"id": overlay_projectversion.id, "name": overlay_projectversion.name}
    )


@app.http_post("/api/projectversions/{projectversion_id}/toggleci")
@req_role("owner")
async def post_projectversion_toggle_ci(request):
    """
    Toggles the ci enabled flag on a projectversion.

    ---
    description: Toggles the ci enabled flag on a projectversion.
    tags:
        - ProjectVersions
    consumes:
        - application/x-www-form-urlencoded
    parameters:
        - name: projectversion_id
          in: path
          required: true
          type: integer
    produces:
        - text/json
    responses:
        "200":
            description: successful
        "500":
            description: internal server error
    """
    projectversion_id = request.match_info["projectversion_id"]
    try:
        projectversion_id = int(projectversion_id)
    except (ValueError, TypeError):
        return ErrorResponse(400, "Incorrect value for projectversion_id")

    projectversion = (
        request.cirrina.db_session.query(ProjectVersion)
        .filter(ProjectVersion.id == projectversion_id)  # pylint: disable=no-member
        .first()
    )

    if not projectversion:
        return ErrorResponse(400, "Projectversion#{projectversion_id} not found".format(
                projectversion_id=projectversion_id))

    projectversion.ci_builds_enabled = not projectversion.ci_builds_enabled
    request.cirrina.db_session.commit()  # pylint: disable=no-member

    result = "enabled" if projectversion.ci_builds_enabled else "disabled"

    logger.info(
        "continuous integration builds %s on ProjectVersion '%s/%s'",
        result,
        projectversion.project.name,
        projectversion.name,
    )

    return web.Response(text="Ci builds are now {}.".format(result), status=200)


@app.http_post("/api/projectversions/{projectversion_id}/lock")
@req_role("owner")
async def post_projectversion_lock(request):
    """
    Locks a projectversion.

    ---
    description: Locks a projectversion.
    tags:
        - ProjectVersions
    consumes:
        - application/x-www-form-urlencoded
    parameters:
        - name: projectversion_id
          in: path
          required: true
          type: integer
    produces:
        - text/json
    responses:
        "200":
            description: successful
        "500":
            description: internal server error
    """
    projectversion_id = request.match_info["projectversion_id"]
    try:
        projectversion_id = int(projectversion_id)
    except (ValueError, TypeError):
        return ErrorResponse(400, "Incorrect value for projectversion_id")

    projectversion = (
        request.cirrina.db_session.query(ProjectVersion)
        .filter(ProjectVersion.id == projectversion_id)  # pylint: disable=no-member
        .first()
    )

    if not projectversion:
        return ErrorResponse(400, "Projectversion#{projectversion_id} not found".format(
                projectversion_id=projectversion_id))

    deps = get_projectversion_deps_manually(projectversion, to_dict=False)
    for dep in deps:
        if not dep.is_locked:
            return ErrorResponse(400, "Dependencies of given projectversion must be locked")

    projectversion.is_locked = True
    projectversion.ci_builds_enabled = False
    request.cirrina.db_session.commit()  # pylint: disable=no-member

    logger.info(
        "ProjectVersion '%s/%s' locked",
        projectversion.project.name,
        projectversion.name,
    )

    return web.Response(text="Locked Project Version", status=200)


@app.http_put("/api/projectversions/{projectversion_id}/mark-delete")
@req_role("owner")
async def mark_delete_projectversion(request):
    """
    Marks a projectversion as deleted.

    ---
    description: Deletes a projectversion.
    tags:
        - ProjectVersions
    consumes:
        - application/x-www-form-urlencoded
    parameters:
        - name: projectversion_id
          in: path
          required: true
          type: integer
    produces:
        - text/json
    responses:
        "200":
            description: successful
        "500":
            description: internal server error
    """
    projectversion_id = request.match_info["projectversion_id"]
    try:
        projectversion_id = int(projectversion_id)
    except (ValueError, TypeError):
        logger.error(
            "projectversion mark delete: invalid projectversion_id %s",
            projectversion_id,
        )
        return ErrorResponse(400, "invalid projectversion_id")

    projectversion = (
        request.cirrina.db_session.query(ProjectVersion)
        .filter(ProjectVersion.id == projectversion_id)  # pylint: disable=no-member
        .first()
    )

    if not projectversion:
        logger.error(
            "projectversion mark delete: projectversion_id %d not found",
            projectversion_id,
        )
        return ErrorResponse(400, "Projectversion#{projectversion_id} not found".format(
                projectversion_id=projectversion_id))

    if projectversion.dependents:
        blocking_dependants = []
        for d in projectversion.dependents:
            if not d.is_deleted:
                blocking_dependants.append("{}/{}".format(d.project.name, d.name))
        if blocking_dependants:
            logger.error(
                "projectversion mark delete: projectversion_id %d still has dependency %d",
                projectversion_id,
                d.id,
            )
            return ErrorResponse(400,
                                 "Projectversions '{}' are still depending on this version, you can not delete it!".format(
                                  ", ".join(blocking_dependants)))

    base_mirror_name = projectversion.basemirror.project.name
    base_mirror_version = projectversion.basemirror.name

    args = {
        "drop_publish": [
            base_mirror_name,
            base_mirror_version,
            projectversion.project.name,
            projectversion.name,
            "stable",
        ]
    }
    await request.cirrina.aptly_queue.put(args)
    args = {
        "drop_publish": [
            base_mirror_name,
            base_mirror_version,
            projectversion.project.name,
            projectversion.name,
            "unstable",
        ]
    }
    await request.cirrina.aptly_queue.put(args)

    projectversion.is_deleted = True
    # lock the projectversion so no packages can be published in this repository
    projectversion.is_locked = True
    projectversion.ci_builds_enabled = False
    request.cirrina.db_session.commit()  # pylint: disable=no-member

    logger.info(
        "ProjectVersion '%s/%s' deleted",
        projectversion.project.name,
        projectversion.name,
    )

    return web.Response(text="Deleted Project Version", status=200)


@app.http_delete("/api/projectversions/{projectversion_id}/dependency")
@req_role("owner")
async def delete_projectversion_dependency(request):
    """
    Deletes a projectversion dependency.

    ---
    description: Deletes a dependency of a projectversion.
    tags:
        - ProjectVersions
    consumes:
        - application/json
    parameters:
        - name: projectversion_id
          in: path
          required: true
          type: integer
        - name: body
          in: body
          required: true
          schema:
            type: object
            properties:
                dependency_id:
                    type: integer
    produces:
        - text/json
    responses:
        "200":
            description: successful
        "500":
            description: internal server error
    """
    params = await request.json()

    projectversion_id = parse_int(request.match_info["projectversion_id"])
    if not projectversion_id:
        return ErrorResponse(400, "Incorrect value for projectversion_id")

    dependency_id = parse_int(params.get("dependency_id"))
    if not dependency_id:
        return ErrorResponse(400, "Incorrect value for dependency_id")

    projectversion = request.cirrina.db_session.query(ProjectVersion).filter(
            ProjectVersion.id == projectversion_id).first()

    if not projectversion:
        return ErrorResponse(400,
                             "Could not find projectversion with id: {}".format(projectversion_id)
                             )

    if projectversion.is_locked:
        return ErrorResponse(400,
                             "You can not delete dependencies on a locked projectversion!"
                             )

    dependency = (
        request.cirrina.db_session.query(ProjectVersion)
        .filter(ProjectVersion.id == dependency_id)
        .first()
    )

    if not dependency:
        return ErrorResponse(400,
                             "Could not find projectversion dependency with id: {}".format(
                                dependency_id
                                )
                             )

    projectversion.dependencies.remove(dependency)

    pv_name = "{}/{}".format(projectversion.project.name, projectversion.name)
    dep_name = "{}/{}".format(dependency.project.name, dependency.name)

    request.cirrina.db_session.commit()  # pylint: disable=no-member
    logger.info("ProjectVersionDependency '%s -> %s' deleted", pv_name, dep_name)

    return web.Response(
        text="Deleted dependency from {} to {}".format(pv_name, dep_name), status=200
    )


@app.http_post("/api/projectversions/{projectversion_id}/dependency")
@app.authenticated
@req_role("owner")
async def post_projectversion_dependency(request):
    """
    Adds a projectversiondependency to a projectversion.

    ---
    description: Return the projectversion
    tags:
        - ProjectVersions
    consumes:
        - application/json
    parameters:
        - name: projectversion_id
          in: path
          required: true
          type: integer
        - name: body
          in: body
          required: true
          schema:
            type: object
            properties:
                dependency_id:
                    type: integer
    produces:
        - text/json
    responses:
        "200":
            description: successful
        "500":
            description: internal server error
    """
    params = await request.json()

    projectversion_id = parse_int(request.match_info["projectversion_id"])
    if not projectversion_id:
        return ErrorResponse(400, "Incorrect value for projectversion_id")

    dependency_id = parse_int(params.get("dependency_id"))
    if not dependency_id:
        return ErrorResponse(400, "Incorrect value for dependency_id")

    projectversion = (
        request.cirrina.db_session.query(ProjectVersion)
        .filter(ProjectVersion.id == projectversion_id)  # pylint: disable=no-member
        .first()
    )
    if not projectversion:
        return ErrorResponse(400, "Invalid data received.")

    if projectversion.is_locked:
        return ErrorResponse(400, "You can not add dependencies on a locked projectversion!")

    if dependency_id == projectversion_id:
        return ErrorResponse(400, "You can not add a dependency of the same projectversion to itself!")

    # check for dependency loops
    dep_ids = get_projectversion_deps(dependency_id, request.cirrina.db_session)
    if projectversion_id in dep_ids:
        return ErrorResponse(400, "You can not add a dependency of a projectversion depending itself on this projectversion!")

    dependency = (
        request.cirrina.db_session.query(ProjectVersion)
        .filter(ProjectVersion.id == dependency_id)  # pylint: disable=no-member
        .first()
    )
    if not dependency:
        return ErrorResponse(400, "Invalid data received.")

    projectversion.dependencies.append(dependency)
    request.cirrina.db_session.commit()

    return web.Response(status=200, text="Dependency added")
