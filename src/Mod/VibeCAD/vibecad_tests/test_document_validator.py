# SPDX-License-Identifier: LGPL-2.1-or-later

from types import SimpleNamespace

from VibeCADDocumentValidator import validate_open_document


class Shape:
    def __init__(self, valid=True, faces=None, solids=None):
        self.valid = valid
        self.Faces = [] if faces is None else faces
        self.Solids = [] if solids is None else solids

    def isNull(self):
        return False

    def isValid(self):
        return self.valid


class Mesh:
    CountPoints = 8
    CountEdges = 18
    CountFacets = 12
    Area = 600.0
    Volume = 1000.0
    BoundBox = SimpleNamespace(
        XMin=0, YMin=0, ZMin=0, XMax=10, YMax=10, ZMax=10,
        XLength=10, YLength=10, ZLength=10,
    )

    def __init__(self, *, solid=True, open_edges=0):
        self.solid = solid
        self.open_edges = open_edges

    def __getattr__(self, name):
        clean_boolean_checks = {
            "hasNonManifolds", "hasSelfIntersections",
            "hasNonUniformOrientedFacets", "hasInvalidPoints",
            "hasInvalidNeighbourhood", "hasPointsOutOfRange",
            "hasFacetsOutOfRange", "hasCorruptedFacets", "hasPointsOnEdge",
        }
        zero_count_checks = {
            "countDuplicatedPoints", "countDuplicatedFacets",
            "countDegeneratedFacets", "countNonUniformOrientedFacets",
        }
        if name in clean_boolean_checks:
            return lambda: False
        if name in zero_count_checks:
            return lambda: 0
        if name == "isSolid":
            return lambda: self.solid
        if name == "countComponents":
            return lambda: 1
        if name == "countOpenEdges":
            return lambda: self.open_edges
        raise AttributeError(name)


def _document(*objects):
    return SimpleNamespace(Objects=list(objects), getRecomputeDiagnostics=lambda: [])


def test_validates_reopenable_techdraw_dependencies():
    source = SimpleNamespace(Name="Bracket", TypeId="PartDesign::Feature", Shape=Shape())
    view = SimpleNamespace(
        Name="View",
        TypeId="TechDraw::DrawViewPart",
        Source=[source],
        getProjectedElementDescriptors=lambda: {"edges": [{"name": "Edge0"}]},
    )
    dimension = SimpleNamespace(
        Name="Dimension",
        TypeId="TechDraw::DrawViewDimension",
        References2D=[(view, "Edge0")],
        getRawValue=lambda: 40.0,
    )
    annotation = SimpleNamespace(
        Name="Annotation",
        TypeId="TechDraw::DrawViewAnnotation",
        Text=["REVISION: 1"],
    )

    result = validate_open_document(_document(source, view, dimension, annotation))

    assert result == {
        "ok": True, "errors": [], "techdraw_checks": 3,
        "assembly_checks": 0, "exploded_view_checks": 0,
        "spreadsheet_checks": 0, "material_checks": 0,
        "mesh_checks": 0,
        "surface_checks": 0,
        "cam_checks": 0,
        "fem_checks": 0,
    }


def test_rejects_broken_techdraw_dependencies_and_invalid_shape():
    source = SimpleNamespace(Name="Broken", TypeId="Part::Feature", Shape=Shape(False))
    view = SimpleNamespace(
        Name="View",
        TypeId="TechDraw::DrawViewPart",
        Source=[],
        getProjectedElementDescriptors=lambda: {"edges": []},
    )
    dimension = SimpleNamespace(
        Name="Dimension",
        TypeId="TechDraw::DrawViewDimension",
        References2D=[],
        getRawValue=lambda: float("nan"),
    )
    annotation = SimpleNamespace(
        Name="Annotation",
        TypeId="TechDraw::DrawViewAnnotation",
        Text=[" "],
    )

    result = validate_open_document(_document(source, view, dimension, annotation))

    assert result["ok"] is False
    assert result["techdraw_checks"] == 3
    assert any("invalid shape" in error for error in result["errors"])
    assert any("no source" in error for error in result["errors"])
    assert any("no references" in error for error in result["errors"])
    assert any("not finite" in error for error in result["errors"])
    assert any("annotation is empty" in error for error in result["errors"])


def test_validates_jointed_assembly_links_grounding_references_and_solver():
    source = SimpleNamespace(Name="Source", TypeId="Part::Feature", Shape=Shape())
    first = SimpleNamespace(
        Name="Part001", TypeId="App::Link", LinkedObject=source, Shape=Shape()
    )
    second = SimpleNamespace(
        Name="Part002", TypeId="App::Link", LinkedObject=source, Shape=Shape()
    )
    ground = SimpleNamespace(Name="Ground", ObjectToGround=first)
    joint = SimpleNamespace(
        Name="Joint", ObjectToGround=None,
        Reference1=(first, ["", ""]), Reference2=(second, ["", ""]),
    )
    joint_group = SimpleNamespace(
        Name="Joints", TypeId="Assembly::JointGroup", Group=[ground, joint]
    )
    assembly = SimpleNamespace(
        Name="Assembly", TypeId="Assembly::AssemblyObject",
        Group=[joint_group, first, second], solve=lambda _verbose: 0,
    )

    result = validate_open_document(
        _document(source, assembly, joint_group, first, second, ground, joint)
    )

    assert result["ok"] is True
    assert result["assembly_checks"] == 3


def test_rejects_unlinked_ungrounded_or_unsolved_assembly():
    source = SimpleNamespace(Name="Source", TypeId="Part::Feature", Shape=Shape())
    first = SimpleNamespace(
        Name="Part001", TypeId="App::Link", LinkedObject=source, Shape=Shape()
    )
    broken = SimpleNamespace(
        Name="Part002", TypeId="App::Link", LinkedObject=None, Shape=Shape()
    )
    joint = SimpleNamespace(
        Name="Joint", ObjectToGround=None,
        Reference1=(first, ["", ""]), Reference2=(broken, ["", ""]),
    )
    joint_group = SimpleNamespace(
        Name="Joints", TypeId="Assembly::JointGroup", Group=[joint]
    )
    assembly = SimpleNamespace(
        Name="Assembly", TypeId="Assembly::AssemblyObject",
        Group=[joint_group, first, broken], solve=lambda _verbose: 3,
    )

    result = validate_open_document(
        _document(source, assembly, joint_group, first, broken, joint)
    )

    assert result["ok"] is False
    assert any("no linked source" in error for error in result["errors"])
    assert any("no grounded component" in error for error in result["errors"])


def test_validates_spreadsheet_alias_expression_links():
    sheet = SimpleNamespace(
        Name="Spreadsheet", Label="Parameters", TypeId="Spreadsheet::Sheet",
        ExpressionEngine=[], getUsedCells=lambda: ["B1"],
        getAlias=lambda cell: "part_width" if cell == "B1" else "",
        get=lambda _cell: 40.0,
    )
    box = SimpleNamespace(
        Name="Box", TypeId="Part::Box", Shape=Shape(),
        ExpressionEngine=[("Length", "Spreadsheet.part_width")],
    )

    result = validate_open_document(_document(box, sheet))

    assert result["ok"] is True
    assert result["spreadsheet_checks"] == 2


def test_rejects_missing_spreadsheet_alias_link():
    sheet = SimpleNamespace(
        Name="Spreadsheet", Label="Parameters", TypeId="Spreadsheet::Sheet",
        ExpressionEngine=[], getUsedCells=lambda: ["B1"],
        getAlias=lambda _cell: "other", get=lambda _cell: 40.0,
    )
    box = SimpleNamespace(
        Name="Box", TypeId="Part::Box", Shape=Shape(),
        ExpressionEngine=[("Length", "<<Parameters>>.part_width")],
    )

    result = validate_open_document(_document(box, sheet))

    assert result["ok"] is False
    assert any("alias part_width is missing" in error for error in result["errors"])


def test_validates_embedded_assigned_material_identity():
    card = SimpleNamespace(UUID="material-1", Name="Aluminum 6061-T6")
    part = SimpleNamespace(
        Name="Part", TypeId="Part::Feature", Shape=Shape(),
        ShapeMaterial=card, ExpressionEngine=[],
    )

    result = validate_open_document(_document(part))

    assert result["ok"] is True and result["material_checks"] == 1


def test_rejects_assigned_material_without_embedded_name():
    part = SimpleNamespace(
        Name="Part", TypeId="Part::Feature", Shape=Shape(),
        ShapeMaterial=SimpleNamespace(UUID="material-1", Name=""),
        ExpressionEngine=[],
    )

    result = validate_open_document(_document(part))

    assert result["ok"] is False
    assert any("has no name" in error for error in result["errors"])


def test_validates_nonempty_watertight_mesh():
    mesh = SimpleNamespace(
        Name="PrintableMesh", TypeId="Mesh::Feature", Mesh=Mesh(),
        ExpressionEngine=[],
    )

    result = validate_open_document(_document(mesh))

    assert result["ok"] is True
    assert result["mesh_checks"] == 1


def test_rejects_open_mesh_from_accepted_document():
    mesh = SimpleNamespace(
        Name="OpenMesh", TypeId="Mesh::Feature",
        Mesh=Mesh(solid=False, open_edges=4), ExpressionEngine=[],
    )

    result = validate_open_document(_document(mesh))

    assert result["ok"] is False
    assert result["mesh_checks"] == 1
    assert any("mesh is not ready" in error for error in result["errors"])
    assert any("open_edges" in error for error in result["errors"])


def test_validates_surface_fill_and_thickened_solid_dependencies():
    wire = SimpleNamespace(Name="Wire", TypeId="Part::Feature", Shape=Shape())
    fill = SimpleNamespace(
        Name="SurfaceFill", TypeId="Surface::Filling", Shape=Shape(faces=[object()]),
        BoundaryEdges=[(wire, ["Edge1"])], ExpressionEngine=[],
    )
    thick = SimpleNamespace(
        Name="Thicken", TypeId="Part::Offset",
        Shape=Shape(faces=[object()], solids=[object()]), Mode="Skin", Fill=True,
        Source=fill, ExpressionEngine=[],
    )

    result = validate_open_document(_document(wire, fill, thick))

    assert result["ok"] is True
    assert result["surface_checks"] == 2


def test_rejects_broken_surface_fill_and_thickening_dependencies():
    fill = SimpleNamespace(
        Name="SurfaceFill", TypeId="Surface::Filling", Shape=Shape(),
        BoundaryEdges=[], ExpressionEngine=[],
    )
    thick = SimpleNamespace(
        Name="Thicken", TypeId="Part::Offset", Shape=Shape(), Mode="Skin",
        Fill=False, Source=None, ExpressionEngine=[],
    )

    result = validate_open_document(_document(fill, thick))

    assert result["ok"] is False
    assert result["surface_checks"] == 2
    assert any("no boundary links" in error for error in result["errors"])
    assert any("has no source" in error for error in result["errors"])
    assert any("not one solid" in error for error in result["errors"])


def test_validates_native_cam_job_stock_tool_and_path_hierarchy():
    model = SimpleNamespace(Name="ModelClone", Shape=Shape(solids=[object()]))
    stock = SimpleNamespace(Name="Stock", Shape=Shape(solids=[object()]))
    tool = SimpleNamespace(Name="ToolBit")
    controller = SimpleNamespace(Name="ToolController", Tool=tool)
    operation = SimpleNamespace(
        Name="FaceOperation", ToolController=controller,
        Path=SimpleNamespace(Commands=[object()]),
    )
    job = SimpleNamespace(
        Name="Job", TypeId="Path::FeaturePython", Shape=Shape(),
        Model=SimpleNamespace(Group=[model]), Stock=stock,
        Tools=SimpleNamespace(Group=[controller]),
        Operations=SimpleNamespace(Group=[operation]), ExpressionEngine=[],
    )

    result = validate_open_document(_document(job))

    assert result["ok"] is True
    assert result["cam_checks"] == 1


def test_rejects_cam_job_with_invalid_stock_unlinked_tool_or_empty_path():
    controller = SimpleNamespace(Name="ToolController", Tool=None)
    operation = SimpleNamespace(
        Name="FaceOperation", ToolController=SimpleNamespace(Name="Foreign"),
        Path=SimpleNamespace(Commands=[]),
    )
    job = SimpleNamespace(
        Name="Job", TypeId="Path::FeaturePython", Shape=Shape(),
        Model=SimpleNamespace(Group=[]), Stock=SimpleNamespace(Name="Stock", Shape=Shape()),
        Tools=SimpleNamespace(Group=[controller]),
        Operations=SimpleNamespace(Group=[operation]), ExpressionEngine=[],
    )

    result = validate_open_document(_document(job))

    assert result["ok"] is False
    assert result["cam_checks"] == 1
    assert any("no model clones" in error for error in result["errors"])
    assert any("stock is not one valid solid" in error for error in result["errors"])
    assert any("controller has no tool" in error for error in result["errors"])
    assert any("path is empty" in error for error in result["errors"])


def test_validates_native_fem_analysis_hierarchy_and_mesh():
    model = SimpleNamespace(Name="Model", TypeId="Part::Feature", Shape=Shape())
    solver = SimpleNamespace(
        Name="Solver", TypeId="Fem::FemSolverObjectPython",
        AnalysisType="static", Proxy=SimpleNamespace(Type="Fem::SolverCcxTools"),
    )
    material = SimpleNamespace(
        Name="Material", TypeId="App::MaterialObjectPython",
        Material={"YoungsModulus": "69000 MPa", "PoissonRatio": "0.33"},
    )
    fixed = SimpleNamespace(
        Name="Fixed", TypeId="Fem::ConstraintFixed", Proxy=None,
        References=[(model, ["Face1"])],
    )
    force = SimpleNamespace(
        Name="Force", TypeId="Fem::ConstraintForce", Proxy=None,
        References=[(model, ["Face2"])],
    )
    fem_mesh = SimpleNamespace(NodeCount=16, VolumeCount=24)
    mesh = SimpleNamespace(
        Name="Mesh", TypeId="Fem::FemMeshShapeBaseObjectPython", FemMesh=fem_mesh,
        VibeCADOperationKind="gmsh", VibeCADOperationState="completed",
        VibeCADOperationFinalized=True,
    )
    analysis = SimpleNamespace(
        Name="Analysis", TypeId="Fem::FemAnalysis",
        Group=[solver, material, fixed, force, mesh],
    )

    result = validate_open_document(
        _document(model, analysis, solver, material, fixed, force, mesh)
    )

    assert result["ok"] is True
    assert result["fem_checks"] == 1


def test_rejects_incomplete_native_fem_analysis():
    solver = SimpleNamespace(
        Name="Solver", TypeId="Fem::FemSolverObjectPython",
        AnalysisType="static", Proxy=SimpleNamespace(Type="Fem::SolverCcxTools"),
        VibeCADOperationKind="calculix", VibeCADOperationState="failed",
        VibeCADOperationFinalized=False, Results=[],
    )
    mesh = SimpleNamespace(
        Name="Mesh", TypeId="Fem::FemMeshShapeBaseObjectPython",
        FemMesh=SimpleNamespace(NodeCount=0, VolumeCount=0),
        VibeCADOperationKind="gmsh", VibeCADOperationState="running",
        VibeCADOperationFinalized=False,
    )
    analysis = SimpleNamespace(
        Name="Analysis", TypeId="Fem::FemAnalysis", Group=[solver, mesh]
    )

    result = validate_open_document(_document(analysis, solver, mesh))

    assert result["ok"] is False
    assert result["fem_checks"] == 1
    assert any("mesh has no nodes" in error for error in result["errors"])
    assert any("has no material" in error for error in result["errors"])
    assert any("has no fixed support" in error for error in result["errors"])
    assert any("CalculiX operation is not complete" in error for error in result["errors"])
