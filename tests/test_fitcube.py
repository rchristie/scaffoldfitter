import logging
import math
import os
import sys
import unittest
from cmlibs.utils.zinc.field import createFieldMeshIntegral
from cmlibs.utils.zinc.finiteelement import evaluate_field_nodeset_mean, find_node_with_name, evaluate_field_nodeset_range
from cmlibs.utils.zinc.region import write_to_buffer, read_from_buffer
from cmlibs.zinc.context import Context
from cmlibs.zinc.field import Field
from cmlibs.zinc.node import Node, Nodeset
from cmlibs.zinc.result import RESULT_OK
from scaffoldfitter.fitter import Fitter
from scaffoldfitter.fitterjson import decodeJSONFitterSteps
from scaffoldfitter.fitterstepalign import FitterStepAlign, createFieldsTransformations
from scaffoldfitter.fitterstepconfig import FitterStepConfig
from scaffoldfitter.fitterstepfit import FitterStepFit


here = os.path.abspath(os.path.dirname(__file__))


logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def assertAlmostEqualList(testcase, actualList, expectedList, delta):
    assert len(actualList) == len(expectedList)
    for actual, expected in zip(actualList, expectedList):
        testcase.assertAlmostEqual(actual, expected, delta=delta)


def getRotationMatrix(eulerAngles):
    """
    From Zinc graphics_library.cpp, transposed.
    :param eulerAngles: 3-component field of angles in radians, components:
    1 = azimuth (about z)
    2 = elevation (about rotated y)
    3 = roll (about rotated x)
    :return: 9-component rotation matrix varying fastest across, suitable for pre-multiplying [x, y, z].
    """
    cos_azimuth = math.cos(eulerAngles[0])
    sin_azimuth = math.sin(eulerAngles[0])
    cos_elevation = math.cos(eulerAngles[1])
    sin_elevation = math.sin(eulerAngles[1])
    cos_roll = math.cos(eulerAngles[2])
    sin_roll = math.sin(eulerAngles[2])
    return [
        cos_azimuth*cos_elevation,
        cos_azimuth*sin_elevation*sin_roll - sin_azimuth*cos_roll,
        cos_azimuth*sin_elevation*cos_roll + sin_azimuth*sin_roll,
        sin_azimuth*cos_elevation,
        sin_azimuth*sin_elevation*sin_roll + cos_azimuth*cos_roll,
        sin_azimuth*sin_elevation*cos_roll - cos_azimuth*sin_roll,
        -sin_elevation,
        cos_elevation*sin_roll,
        cos_elevation*cos_roll
        ]


def transformCoordinatesList(xIn: list, transformationMatrix, translation):
    """
    Transforms coordinates by multiplying by 9-component transformationMatrix
    then offsetting by translation.
    :xIn: List of 3-D coordinates to transform:
    :return: List of 3-D transformed coordinates.
    """
    assert (len(xIn) > 0) and (len(xIn[0]) == 3) and (len(transformationMatrix) == 9) and (len(translation) == 3)
    xOut = []
    for x in xIn:
        x2 = []
        for c in range(3):
            v = translation[c]
            for d in range(3):
                v += transformationMatrix[c*3 + d]*x[d]
            x2.append(v)
        xOut.append(x2)
    return xOut


def getNodesetConditionalSize(nodeset: Nodeset, conditionalField: Field):
    """
    :return: Number of objects in nodeset for which conditionalField is True.
    """
    assert conditionalField.getNumberOfComponents() == 1
    fieldmodule = conditionalField.getFieldmodule()
    fieldcache = fieldmodule.createFieldcache()
    nodeiterator = nodeset.createNodeiterator()
    size = 0
    node = nodeiterator.next()
    while node.isValid():
        fieldcache.setNode(node)
        result, value = conditionalField.evaluateReal(fieldcache, 1)
        if value != 0.0:
            size += 1
        node = nodeiterator.next()
    return size


class FitCubeToSphereTestCase(unittest.TestCase):

    def test_alignFixedRandomData(self):
        """
        Test alignment of model and data to known transformations.
        """
        zinc_model_file = os.path.join(here, "resources", "cube_to_sphere.exf")
        zinc_data_file = os.path.join(here, "resources", "cube_to_sphere_data_random.exf")
        fitter = Fitter(zinc_model_file, zinc_data_file)
        fitter.setDiagnosticLevel(1)
        fitter.load()
        dataScale = fitter.getDataScale()
        self.assertAlmostEqual(dataScale, 1.0, delta=1.0E-7)

        self.assertEqual(fitter.getModelCoordinatesField().getName(), "coordinates")
        self.assertEqual(fitter.getDataCoordinatesField().getName(), "data_coordinates")
        self.assertEqual(fitter.getMarkerGroup().getName(), "marker")

        fieldmodule = fitter.getFieldmodule()
        nodes = fieldmodule.findNodesetByFieldDomainType(Field.DOMAIN_TYPE_NODES)
        coordinates = fieldmodule.findFieldByName("coordinates")
        groupCentre1 = {}
        for groupName in ["bottom", "sides", "top"]:
            groupCentre1[groupName] = evaluate_field_nodeset_mean(
                coordinates, fieldmodule.findFieldByName(groupName).castGroup().getNodesetGroup(nodes))
        assertAlmostEqualList(self, groupCentre1["bottom"], [0.5, 0.5, 0.0], delta=1.0E-7)
        assertAlmostEqualList(self, groupCentre1["sides"], [0.5, 0.5, 0.5], delta=1.0E-7)
        assertAlmostEqualList(self, groupCentre1["top"], [0.5, 0.5, 1.0], delta=1.0E-7)
        align = FitterStepAlign()
        fitter.addFitterStep(align)
        align.setScale(1.1)
        align.setTranslation([0.1, -0.2, 0.3])
        align.setRotation([math.pi/4.0, math.pi/8.0, math.pi/2.0])
        self.assertFalse(align.isAlignMarkers())
        align.run()
        rotation = align.getRotation()
        scale = align.getScale()
        translation = align.getTranslation()
        rotationMatrix = getRotationMatrix(rotation)
        transformationMatrix = [v*scale for v in rotationMatrix]
        bottomCentre2Expected, sidesCentre2Expected, topCentre2Expected = transformCoordinatesList(
            [groupCentre1["bottom"], groupCentre1["sides"], groupCentre1["top"]], transformationMatrix, translation)
        groupCentre2 = {}
        for groupName in ["bottom", "sides", "top"]:
            groupCentre2[groupName] = evaluate_field_nodeset_mean(
                coordinates, fieldmodule.findFieldByName(groupName).castGroup().getNodesetGroup(nodes))
        assertAlmostEqualList(self, groupCentre2["bottom"], bottomCentre2Expected, delta=1.0E-7)
        assertAlmostEqualList(self, groupCentre2["sides"], sidesCentre2Expected, delta=1.0E-7)
        assertAlmostEqualList(self, groupCentre2["top"], topCentre2Expected, delta=1.0E-7)

    def test_alignMarkersFitRegularData(self):
        """
        Test automatic alignment of model and data using fiducial markers.
        """
        zinc_model_file = os.path.join(here, "resources", "cube_to_sphere.exf")
        zinc_data_file = os.path.join(here, "resources", "cube_to_sphere_data_regular.exf")
        fitter = Fitter(zinc_model_file, zinc_data_file)
        self.assertEqual(1, len(fitter.getFitterSteps()))  # there is always an initial FitterStepConfig
        fitter.setDiagnosticLevel(1)
        fitter.load()
        dataScale = fitter.getDataScale()
        self.assertAlmostEqual(dataScale, 1.0, delta=1.0E-7)

        coordinates = fitter.getModelCoordinatesField()
        self.assertEqual(coordinates.getName(), "coordinates")
        self.assertEqual(fitter.getDataCoordinatesField().getName(), "data_coordinates")
        self.assertEqual(fitter.getMarkerGroup().getName(), "marker")
        # fitter.getRegion().writeFile(os.path.join(here, "resources", "km_fitgeometry1.exf"))
        fieldmodule = fitter.getFieldmodule()
        surfaceAreaField = createFieldMeshIntegral(coordinates, fitter.getMesh(2), number_of_points=4)
        volumeField = createFieldMeshIntegral(coordinates, fitter.getMesh(3), number_of_points=3)
        fieldcache = fieldmodule.createFieldcache()
        result, surfaceArea = surfaceAreaField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        result, volume = volumeField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        self.assertAlmostEqual(surfaceArea, 6.0, delta=1.0E-6)
        self.assertAlmostEqual(volume, 1.0, delta=1.0E-7)
        activeNodeset = fitter.getActiveDataNodesetGroup()
        self.assertEqual(292, activeNodeset.getSize())
        groupSizes = {"bottom": 72, "sides": 144, "top": 72, "marker": 4}
        for groupName, count in groupSizes.items():
            self.assertEqual(count, getNodesetConditionalSize(
                activeNodeset, fitter.getFieldmodule().findFieldByName(groupName)))

        align = FitterStepAlign()
        fitter.addFitterStep(align)
        self.assertEqual(2, len(fitter.getFitterSteps()))
        self.assertTrue(align.setAlignMarkers(True))
        self.assertTrue(align.isAlignMarkers())
        self.assertTrue(align.canAlignMarkers())
        self.assertTrue(align.canAlignGroups())
        self.assertTrue(align.canAutoAlign())
        self.assertEqual(4, align.matchingMarkerCount())
        self.assertEqual(3, align.matchingGroupCount())

        align.run()
        # fitter.getRegion().writeFile(os.path.join(here, "resources", "km_fitgeometry2.exf"))
        rotation = align.getRotation()
        scale = align.getScale()
        translation = align.getTranslation()
        assertAlmostEqualList(self, rotation, [-0.25*math.pi, 0.0, 0.0], delta=1.0E-4)
        self.assertAlmostEqual(scale, 0.8047378476539072, places=5)
        assertAlmostEqualList(self, translation,
                              [-0.5690355950594247, 1.1068454682130484e-05, -0.4023689233125251], delta=1.0E-6)
        result, surfaceArea = surfaceAreaField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        result, volume = volumeField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        self.assertAlmostEqual(surfaceArea, 3.885618020657802, delta=1.0E-6)
        self.assertAlmostEqual(volume, 0.5211506471189844, delta=1.0E-6)

        fit1 = FitterStepFit()
        fitter.addFitterStep(fit1)
        self.assertEqual(3, len(fitter.getFitterSteps()))
        fit1.setGroupDataWeight("marker", 1.0)
        # set sliding factor to equivalent used at time of test creation
        fit1.setGroupDataSlidingFactor(None, 1.0)
        fit1.setGroupCurvaturePenalty(None, [0.01])
        fit1.setNumberOfIterations(3)
        fit1.setUpdateReferenceState(True)
        fit1.run()
        # fitter.getRegion().writeFile(os.path.join(here, "resources", "km_fitgeometry3.exf"))

        result, surfaceArea = surfaceAreaField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        result, volume = volumeField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        self.assertAlmostEqual(surfaceArea, 3.18921662820759, delta=1.0E-4)
        self.assertAlmostEqual(volume, 0.5276212500501103, delta=1.0E-4)

        # test json serialisation
        s = fitter.encodeSettingsJSON()
        fitter2 = Fitter(zinc_model_file, zinc_data_file)
        fitter2.decodeSettingsJSON(s, decodeJSONFitterSteps)
        fitterSteps = fitter2.getFitterSteps()
        self.assertEqual(3, len(fitterSteps))
        self.assertTrue(isinstance(fitterSteps[0], FitterStepConfig))
        self.assertTrue(isinstance(fitterSteps[1], FitterStepAlign))
        self.assertTrue(isinstance(fitterSteps[2], FitterStepFit))
        # fitter2.load()
        # for fitterStep in fitterSteps:
        #    fitterStep.run()
        s2 = fitter.encodeSettingsJSON()
        self.assertEqual(s, s2)

    def test_alignMarkersScaleProportion(self):
        """
        Test automatic alignment of model and data using fiducial markers, using scale proportion 0.9.
        """
        zinc_model_file = os.path.join(here, "resources", "cube_to_sphere.exf")
        zinc_data_file = os.path.join(here, "resources", "cube_to_sphere_data_regular.exf")
        fitter = Fitter(zinc_model_file, zinc_data_file)
        fitter.load()

        align = FitterStepAlign()
        fitter.addFitterStep(align)
        self.assertEqual(2, len(fitter.getFitterSteps()))
        self.assertTrue(align.setAlignMarkers(True))
        self.assertTrue(align.isAlignMarkers())
        scaleProportion = 0.9
        self.assertTrue(align.setScaleProportion(scaleProportion))
        self.assertEqual(scaleProportion, align.getScaleProportion())
        align.run()

        scale = align.getScale()
        self.assertAlmostEqual(scale, scaleProportion * 0.8047378476539072, places=5)

    def test_alignGroupsFitEllipsoidRegularData(self):
        """
        Test automatic alignment of model and data using groups & fit two cubes model to ellipsoid data.
        """
        zinc_model_file = os.path.join(here, "resources", "two_cubes_hermite_nocross_groups.exf")
        zinc_data_file = os.path.join(here, "resources", "two_cubes_ellipsoid_data_regular.exf")
        fitter = Fitter(zinc_model_file, zinc_data_file)
        fitter.setDiagnosticLevel(1)
        fitter.load()

        coordinates = fitter.getModelCoordinatesField()
        self.assertEqual(coordinates.getName(), "coordinates")
        self.assertEqual(fitter.getDataCoordinatesField().getName(), "data_coordinates")
        fieldmodule = fitter.getFieldmodule()
        # surface area includes interior surface in this case
        surfaceAreaField = createFieldMeshIntegral(coordinates, fitter.getMesh(2), number_of_points=4)
        volumeField = createFieldMeshIntegral(coordinates, fitter.getMesh(3), number_of_points=3)
        fieldcache = fieldmodule.createFieldcache()
        result, surfaceArea = surfaceAreaField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        result, volume = volumeField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        self.assertAlmostEqual(surfaceArea, 11.0, delta=1.0E-6)
        self.assertAlmostEqual(volume, 2.0, delta=1.0E-6)

        align = FitterStepAlign()
        fitter.addFitterStep(align)
        self.assertEqual(2, len(fitter.getFitterSteps()))
        self.assertTrue(align.setAlignGroups(True))
        self.assertTrue(align.isAlignGroups())
        align.run()
        rotation = align.getRotation()
        scale = align.getScale()
        translation = align.getTranslation()
        assertAlmostEqualList(self, rotation, [0.0, 0.0, 0.0], delta=1.0E-5)
        self.assertAlmostEqual(scale, 1.0035758865289246, places=5)
        assertAlmostEqualList(self, translation, [-1.003575885551429, -0.5017879470320555, -0.5017879414518605],
                              delta=1.0E-6)
        result, surfaceArea = surfaceAreaField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        result, volume = volumeField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        self.assertAlmostEqual(surfaceArea, 11.0*scale*scale, delta=1.0E-6)
        self.assertAlmostEqual(volume, 2.0*scale*scale*scale, delta=1.0E-6)

        fit1 = FitterStepFit()
        fitter.addFitterStep(fit1)
        self.assertEqual(3, len(fitter.getFitterSteps()))
        strainPenalty, locallySet, inheritable = fit1.getGroupStrainPenalty(None)
        assertAlmostEqualList(self, strainPenalty, [0.0], delta=1.0E-7)
        self.assertFalse(locallySet)
        self.assertFalse(inheritable)
        curvaturePenalty, locallySet, inheritable = fit1.getGroupCurvaturePenalty(None)
        assertAlmostEqualList(self, curvaturePenalty, [0.0], delta=1.0E-7)
        self.assertFalse(locallySet)
        self.assertFalse(inheritable)
        fit1.setGroupStrainPenalty(None, [0.1])
        strainPenalty, locallySet, inheritable = fit1.getGroupStrainPenalty(None)
        assertAlmostEqualList(self, strainPenalty, [0.1], delta=1.0E-7)
        self.assertTrue(locallySet)
        self.assertFalse(inheritable)
        fit1.setGroupCurvaturePenalty(None, [0.01])
        curvaturePenalty, locallySet, inheritable = fit1.getGroupCurvaturePenalty(None)
        assertAlmostEqualList(self, curvaturePenalty, [0.01], delta=1.0E-7)
        self.assertTrue(locallySet)
        self.assertFalse(inheritable)
        # test specifying number of components:
        curvaturePenalty, locallySet, inheritable = fit1.getGroupCurvaturePenalty(None, count=5)
        assertAlmostEqualList(self, curvaturePenalty, [0.01, 0.01, 0.01, 0.01, 0.01], delta=1.0E-7)
        # group "two" strain penalty will initially fall back to default value
        strainPenalty, locallySet, inheritable = fit1.getGroupStrainPenalty("two")
        assertAlmostEqualList(self, strainPenalty, [0.1], delta=1.0E-7)
        self.assertFalse(locallySet)
        self.assertTrue(inheritable)
        fit1.setGroupStrainPenalty("two", [0.1, 0.1, 0.1, 0.1, 20.0, 0.1, 0.1, 20.0, 2.0])
        strainPenalty, locallySet, inheritable = fit1.getGroupStrainPenalty("two")
        assertAlmostEqualList(self, strainPenalty, [0.1, 0.1, 0.1, 0.1, 20.0, 0.1, 0.1, 20.0, 2.0], delta=1.0E-7)
        self.assertTrue(locallySet)
        self.assertTrue(inheritable)
        fit1.setNumberOfIterations(1)
        # set sliding factor to equivalent used at time of test creation
        fit1.setGroupDataSlidingFactor(None, 1.0)
        fit1.run()
        result, surfaceArea = surfaceAreaField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        result, volume = volumeField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        self.assertAlmostEqual(surfaceArea, 10.68348915904981, delta=1.0E-4)
        self.assertAlmostEqual(volume, 2.1574515155924283, delta=1.0E-4)

        # test fibre orientation field
        fitter.load()
        fieldmodule = fitter.getFieldmodule()
        self.assertEqual(None, fitter.getFibreField())
        fibreField = fieldmodule.createFieldConstant([0.0, 0.0, 0.25*math.pi])
        fibreField.setName("custom fibres")
        fibreField.setManaged(True)
        fitter.setFibreField(fibreField)
        self.assertEqual(fibreField, fitter.getFibreField())
        coordinates = fitter.getModelCoordinatesField()
        align.run()
        fit1.run()
        # get end node coordinate to prove twist 
        nodeExpectedCoordinates = {
            3: [0.789915975359949, -0.47623734167234666, -0.5068423371261838],
            6: [0.7837771099139238, 0.48421146804863563, -0.5263573197472752],
            9: [0.7837771089771484, -0.4842114652148681, 0.5263573199420993],
            12: [0.7899159763878485, 0.4762373443863354, 0.5068423367634244]}
        fieldcache = fieldmodule.createFieldcache()
        nodes = fieldmodule.findNodesetByFieldDomainType(Field.DOMAIN_TYPE_NODES)
        for nodeIdentifier, expectedCoordinates in nodeExpectedCoordinates.items():
            node = nodes.findNodeByIdentifier(nodeIdentifier)
            self.assertEqual(RESULT_OK, fieldcache.setNode(node))
            result, x = coordinates.getNodeParameters(fieldcache, -1, Node.VALUE_LABEL_VALUE, 1, 3)
            self.assertEqual(RESULT_OK, result)
            assertAlmostEqualList(self, x, expectedCoordinates, delta=1.0E-6)

        # test inheritance and override of penalties
        fit2 = FitterStepFit()
        fitter.addFitterStep(fit2)
        self.assertEqual(4, len(fitter.getFitterSteps()))
        strainPenalty, locallySet, inheritable = fit2.getGroupStrainPenalty(None)
        assertAlmostEqualList(self, strainPenalty, [0.1], delta=1.0E-7)
        self.assertFalse(locallySet)
        self.assertTrue(inheritable)
        curvaturePenalty, locallySet, inheritable = fit2.getGroupCurvaturePenalty(None)
        assertAlmostEqualList(self, curvaturePenalty, [0.01], delta=1.0E-7)
        self.assertFalse(locallySet)
        self.assertTrue(inheritable)
        fit2.setGroupCurvaturePenalty(None, None)
        curvaturePenalty, locallySet, inheritable = fit2.getGroupCurvaturePenalty(None)
        assertAlmostEqualList(self, curvaturePenalty, [0.0], delta=1.0E-7)
        self.assertTrue(locallySet is None)
        self.assertTrue(inheritable)
        strainPenalty, locallySet, inheritable = fit2.getGroupStrainPenalty("two")
        assertAlmostEqualList(self, strainPenalty, [0.1, 0.1, 0.1, 0.1, 20.0, 0.1, 0.1, 20.0, 2.0], delta=1.0E-7)
        self.assertFalse(locallySet)
        self.assertTrue(inheritable)
        fit2.setGroupStrainPenalty("two", [0.5, 0.9, 0.2])
        strainPenalty, locallySet, inheritable = fit2.getGroupStrainPenalty("two", count=9)
        assertAlmostEqualList(self, strainPenalty, [0.5, 0.9, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2], delta=1.0E-7)
        self.assertTrue(locallySet)
        self.assertTrue(inheritable)

        # test json serialisation
        s = fitter.encodeSettingsJSON()
        fitter2 = Fitter(zinc_model_file, zinc_data_file)
        fitter2.decodeSettingsJSON(s, decodeJSONFitterSteps)
        fitterSteps = fitter2.getFitterSteps()
        self.assertEqual(4, len(fitterSteps))
        self.assertTrue(isinstance(fitterSteps[0], FitterStepConfig))
        self.assertTrue(isinstance(fitterSteps[1], FitterStepAlign))
        self.assertTrue(isinstance(fitterSteps[2], FitterStepFit))
        self.assertTrue(isinstance(fitterSteps[3], FitterStepFit))
        fit1 = fitterSteps[2]
        strainPenalty, locallySet, inheritable = fit1.getGroupStrainPenalty("two")
        assertAlmostEqualList(self, strainPenalty, [0.1, 0.1, 0.1, 0.1, 20.0, 0.1, 0.1, 20.0, 2.0], delta=1.0E-7)
        self.assertTrue(locallySet)
        self.assertTrue(inheritable)
        fit2 = fitterSteps[3]
        curvaturePenalty, locallySet, inheritable = fit2.getGroupCurvaturePenalty(None)
        assertAlmostEqualList(self, curvaturePenalty, [0.0], delta=1.0E-7)
        self.assertTrue(locallySet is None)
        self.assertTrue(inheritable)
        strainPenalty, locallySet, inheritable = fit2.getGroupStrainPenalty("two")
        assertAlmostEqualList(self, strainPenalty, [0.5, 0.9, 0.2], delta=1.0E-7)
        self.assertTrue(locallySet)
        self.assertTrue(inheritable)
        s2 = fitter.encodeSettingsJSON()
        self.assertEqual(s, s2)

        min_jac_el, min_jac_value = fitter.getLowestElementJacobian()
        self.assertEqual(1, min_jac_el)
        self.assertAlmostEqual(0.1869875394, min_jac_value)

    def test_fitRegularDataGroupWeight(self):
        """
        Test fitting with variable data group weights and sliding factors.
        """
        zinc_model_file = os.path.join(here, "resources", "cube_to_sphere.exf")
        zinc_data_file = os.path.join(here, "resources", "cube_to_sphere_data_regular.exf")
        fitter = Fitter(zinc_model_file, zinc_data_file)
        self.assertEqual(1, len(fitter.getFitterSteps()))  # there is always an initial FitterStepConfig
        fitter.setDiagnosticLevel(1)
        fitter.load()

        coordinates = fitter.getModelCoordinatesField()
        self.assertEqual(coordinates.getName(), "coordinates")
        fieldmodule = fitter.getFieldmodule()
        surfaceAreaField = createFieldMeshIntegral(coordinates, fitter.getMesh(2), number_of_points=4)
        volumeField = createFieldMeshIntegral(coordinates, fitter.getMesh(3), number_of_points=3)
        fieldcache = fieldmodule.createFieldcache()

        align = FitterStepAlign()
        fitter.addFitterStep(align)
        self.assertEqual(2, len(fitter.getFitterSteps()))
        self.assertTrue(align.setAlignMarkers(True))
        align.run()

        fit1 = FitterStepFit()
        fitter.addFitterStep(fit1)
        self.assertEqual(3, len(fitter.getFitterSteps()))
        fit1.setGroupDataWeight("bottom", 0.5)
        fit1.setGroupDataWeight("sides", 0.1)
        fit1.setGroupDataSlidingFactor("bottom", 0.4)
        fit1.setGroupDataSlidingFactor("sides", 0.6)
        fit1.setGroupDataSlidingFactor(None, 0.1)
        groupNames = fit1.getGroupSettingsNames()
        self.assertEqual(3, len(groupNames))
        self.assertEqual((0.5, True, False), fit1.getGroupDataWeight("bottom"))
        self.assertEqual((0.1, True, False), fit1.getGroupDataWeight("sides"))
        self.assertEqual((0.4, True, True), fit1.getGroupDataSlidingFactor("bottom"))
        self.assertEqual((0.6, True, True), fit1.getGroupDataSlidingFactor("sides"))
        self.assertEqual((0.1, True, False), fit1.getGroupDataSlidingFactor(None))
        fit1.setGroupCurvaturePenalty(None, [0.01])
        fit1.setNumberOfIterations(1)  # only first iteration is not subject to find mesh location algorithm changes
        fit1.setUpdateReferenceState(False)
        fit1.run()
        dataWeightField = fieldmodule.findFieldByName("data_weight").castFiniteElement()
        self.assertTrue(dataWeightField.isValid())

        groupData = {
            "bottom": (72, [0.2, 0.2, 0.5]),
            "sides": (144, [0.06, 0.06, 0.1]),
            "top": (72, [0.1, 0.1, 1.0])
        }
        for groupName in groupData.keys():
            expectedSize, expectedOrientedWeight = groupData[groupName]
            group = fieldmodule.findFieldByName(groupName).castGroup()
            dataGroup = fitter.getGroupDataProjectionNodesetGroup(group)
            size = dataGroup.getSize()
            self.assertEqual(size, expectedSize)
            dataIterator = dataGroup.createNodeiterator()
            node = dataIterator.next()
            while node.isValid():
                fieldcache.setNode(node)
                result, orientedWeight = dataWeightField.evaluateReal(fieldcache, 3)
                self.assertEqual(result, RESULT_OK)
                assertAlmostEqualList(self, orientedWeight, expectedOrientedWeight, delta=1.0E-10)
                node = dataIterator.next()

        result, surfaceArea = surfaceAreaField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        result, volume = volumeField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        self.assertAlmostEqual(surfaceArea, 3.3460139217396243, delta=1.0E-4)
        self.assertAlmostEqual(volume, 0.5217478266861153, delta=1.0E-4)

        # subsequent iterations produce slightly different results when the find mesh location algorithm changes

        fit2 = FitterStepFit()
        fitter.addFitterStep(fit2)
        self.assertEqual(4, len(fitter.getFitterSteps()))
        fit2.setNumberOfIterations(2)
        fit2.setUpdateReferenceState(True)
        fit2.run()

        result, surfaceArea = surfaceAreaField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        result, volume = volumeField.evaluateReal(fieldcache, 1)
        self.assertEqual(result, RESULT_OK)
        self.assertAlmostEqual(surfaceArea, 3.187490694645035, delta=1.0E-4)
        self.assertAlmostEqual(volume, 0.5072619397447008, delta=1.0E-4)

        min_jac_el, min_jac_value = fitter.getLowestElementJacobian()
        self.assertEqual(1, min_jac_el)
        self.assertAlmostEqual(1.0, min_jac_value)

    def test_groupSettings(self):
        """
        Test per-group settings, and inheritance from previous 
        """
        zinc_model_file = os.path.join(here, "resources", "cube_to_sphere.exf")
        zinc_data_file = os.path.join(here, "resources", "cube_to_sphere_data_regular.exf")
        fitter = Fitter(zinc_model_file, zinc_data_file)
        fitter.setDiagnosticLevel(1)
        config1 = fitter.getInitialFitterStepConfig()
        groupNames = config1.getGroupSettingsNames()
        self.assertEqual(0, len(groupNames))
        self.assertEqual((1.0, False, False), config1.getGroupDataProportion("sides"))
        config1.setGroupDataProportion("sides", -0.1)
        groupNames = config1.getGroupSettingsNames()
        self.assertEqual(1, len(groupNames))
        self.assertEqual((0.0, True, False), config1.getGroupDataProportion("sides"))
        config1.setGroupDataProportion("sides", 0.25)
        config1.setGroupDataProportion("sides", "A")
        config1.setGroupDataProportion("top", 0.4)
        groupNames = config1.getGroupSettingsNames()
        self.assertEqual(2, len(groupNames))
        self.assertTrue("sides" in groupNames)
        self.assertTrue("top" in groupNames)
        self.assertEqual((0.25, True, False), config1.getGroupDataProportion("sides"))
        self.assertEqual((0.4, True, False), config1.getGroupDataProportion("top"))
        self.assertEqual((1.0, False, False), config1.getGroupDataProportion("bottom"))
        config1.setGroupDataProportion("bottom", 0.1)
        self.assertEqual((0.1, True, False), config1.getGroupDataProportion("bottom"))
        groupNames = config1.getGroupSettingsNames()
        self.assertEqual(3, len(groupNames))
        # setting a non-inheriting value to None clears it:
        config1.setGroupDataProportion("bottom", None)
        self.assertEqual((1.0, False, False), config1.getGroupDataProportion("bottom"))
        groupNames = config1.getGroupSettingsNames()
        self.assertEqual(2, len(groupNames))
        config1.setGroupDataProportion("bottom", 0.12)
        self.assertEqual((0.12, True, False), config1.getGroupDataProportion("bottom"))
        groupNames = config1.getGroupSettingsNames()
        self.assertEqual(3, len(groupNames))
        config1.clearGroupDataProportion("bottom")
        self.assertEqual((1.0, False, False), config1.getGroupDataProportion("bottom"))
        groupNames = config1.getGroupSettingsNames()
        self.assertEqual(2, len(groupNames))
        self.assertTrue("sides" in groupNames)
        self.assertTrue("top" in groupNames)
        fitter.load()
        activeNodeset = fitter.getActiveDataNodesetGroup()
        self.assertEqual(141, activeNodeset.getSize())
        groupSizes = {"bottom": 72, "sides": 36, "top": 29, "marker": 4}
        for groupName, count in groupSizes.items():
            self.assertEqual(count, getNodesetConditionalSize(
                activeNodeset, fitter.getFieldmodule().findFieldByName(groupName)))

        groupErrors = {
            "bottom": (0.47716552515635985, 0.5001722675609085, math.inf),
            "sides": (0.3721661947669986, 0.5000286856904854, math.inf),
            "top": (0.5699148581001906, 0.6755409758922845, math.inf),
            "marker": (0.9354143466934853, 1.224744871391589, math.inf)
        }
        for groupName, errors in groupErrors.items():
            rms_error, max_error = fitter.getDataRMSAndMaximumProjectionErrorForGroup(groupName)
            self.assertAlmostEqual(rms_error, errors[0], 5)
            self.assertAlmostEqual(max_error, errors[1], 5)
            jac_det_el, jac_det_value = fitter.getLowestElementJacobianForGroup(groupName)
            self.assertAlmostEqual(jac_det_value, errors[2], 5)

        rms_error, max_error = fitter.getDataRMSAndMaximumProjectionErrorForGroup('left')
        self.assertEqual(None, rms_error)
        self.assertEqual(None, max_error)
        jac_det_el, jac_det_value = fitter.getLowestElementJacobianForGroup('left')
        self.assertIsNone(jac_det_value)

        # test override and inherit
        config2 = FitterStepConfig()
        fitter.addFitterStep(config2)
        config2.setGroupDataProportion("top", None)
        groupNames = config2.getGroupSettingsNames()
        self.assertEqual(1, len(groupNames))
        self.assertTrue("top" in groupNames)
        self.assertEqual((0.25, False, True), config2.getGroupDataProportion("sides"))
        # test that the reset proportion has setLocally None
        self.assertEqual((1.0, None, True), config2.getGroupDataProportion("top"))
        config2.run()
        activeNodeset = fitter.getActiveDataNodesetGroup()
        self.assertEqual(184, activeNodeset.getSize())
        groupSizes = {"bottom": 72, "sides": 36, "top": 72, "marker": 4}
        for groupName, count in groupSizes.items():
            self.assertEqual(count, getNodesetConditionalSize(
                activeNodeset, fitter.getFieldmodule().findFieldByName(groupName)))
        # test inherit through 2 previous configs and cancel/None in config2
        config3 = FitterStepConfig()
        fitter.addFitterStep(config3)
        groupNames = config3.getGroupSettingsNames()
        self.assertEqual(0, len(groupNames))
        self.assertEqual((0.25, False, True), config3.getGroupDataProportion("sides"))
        self.assertEqual((1.0, False, True), config3.getGroupDataProportion("top"))
        config3.run()
        activeNodeset = fitter.getActiveDataNodesetGroup()
        self.assertEqual(184, activeNodeset.getSize())
        for groupName, count in groupSizes.items():
            self.assertEqual(count, getNodesetConditionalSize(
                activeNodeset, fitter.getFieldmodule().findFieldByName(groupName)))
        del config1
        del config2
        del config3

        # test json serialisation
        s = fitter.encodeSettingsJSON()
        fitter2 = Fitter(zinc_model_file, zinc_data_file)
        fitter2.decodeSettingsJSON(s, decodeJSONFitterSteps)
        fitterSteps = fitter2.getFitterSteps()
        self.assertEqual(3, len(fitterSteps))
        config1, config2, config3 = fitterSteps
        self.assertTrue(isinstance(config1, FitterStepConfig))
        self.assertTrue(isinstance(config2, FitterStepConfig))
        self.assertTrue(isinstance(config3, FitterStepConfig))
        groupNames = config1.getGroupSettingsNames()
        self.assertEqual(2, len(groupNames))
        self.assertTrue("sides" in groupNames)
        self.assertTrue("top" in groupNames)
        self.assertEqual((0.25, True, False), config1.getGroupDataProportion("sides"))
        self.assertEqual((0.4, True, False), config1.getGroupDataProportion("top"))
        self.assertEqual((1.0, False, False), config1.getGroupDataProportion("bottom"))
        groupNames = config2.getGroupSettingsNames()
        self.assertEqual(1, len(groupNames))
        self.assertTrue("top" in groupNames)
        self.assertEqual((0.25, False, True), config2.getGroupDataProportion("sides"))
        self.assertEqual((1.0, None, True), config2.getGroupDataProportion("top"))

    def test_preAlignment(self):
        """
        Test prealignment step to ensure models at different translation, scale and rotation all return close
        to same aligned model.
        """
        zinc_model_file = os.path.join(here, "resources", "cube_to_sphere.exf")
        zinc_data_file = os.path.join(here, "resources", "cube_to_sphere_data_random.exf")
        fitter = Fitter(zinc_model_file, zinc_data_file)
        self.assertEqual(1, len(fitter.getFitterSteps()))
        fitter.setDiagnosticLevel(1)

        # Rotation, scale, translation
        transformationList = [
            [[0.0, 0.0, 0.0], 1.0, [0.0, 0.0, 0.0]],
            [[math.pi * 20/180, 0.0, 0.0], 1.0, [0.0, 0.0, 0.0]],
            [[math.pi * 135/180, 0.0, 0.0], 1.0, [0.0, 0.0, 0.0]],
            [[math.pi * 250/180, math.pi * -45/180, 0.0], 1.0, [0.0, 0.0, 0.0]],
            [[math.pi * 45/180, math.pi * 45/180, math.pi * 45/180], 1.0, [0.0, 0.0, 0.0]],
            [[0.0, 0.0, 0.0], 0.05, [0.0, 0.0, 0.0]],
            [[math.pi * 70/180, math.pi * 10/180, math.pi * -300/180], 0.2, [0.0, 0.0, 0.0]],
            [[0.0, 0.0, 0.0], 1.0, [15.0, 15.0, 15.0]],
            [[0.0, 0.0, 0.0], 20.0, [50.0, 0.0, 10.0]],
            [[math.pi * 90/180, math.pi * 200/180, math.pi * 5/180], 1.0, [-10.0, -20.0, 100.0]],
            [[math.pi * -45/180, math.pi * 120/180, math.pi * 10/180], 500.0, [100.0, 100.0, 100.0]]]

        expectedAlignedNodes = [[-0.5690355951820659, 1.1070979208244695e-05, -0.40236892417087866],
                                [-1.1077595833408616e-05, -0.5690355904946871, -0.4023689227447479],
                                [1.1066291829453512e-05, 0.5690355885654408, -0.4023689255966489],
                                [0.569035583878062, -1.1072908454692232e-05, -0.4023689241705181],
                                [-0.5690355951822816, 1.1072995806778281e-05, 0.4023689241678401],
                                [-1.107759604912495e-05, -0.5690355884780887, 0.40236892559397086],
                                [1.10662916138482e-05, 0.5690355905820392, 0.4023689227420698],
                                [0.5690355838778464, -1.1070891856158648e-05, 0.4023689241682007]]

        align = FitterStepAlign()
        fitter.addFitterStep(align)
        self.assertTrue(align.setAlignMarkers(True))
        self.assertTrue(align.isAlignMarkers())

        for i in range(len(transformationList)):
            fitter.load()

            fieldmodule = fitter.getFieldmodule()
            fieldcache = fieldmodule.createFieldcache()
            modelCoordinates = fitter.getModelCoordinatesField()

            rotation = transformationList[i][0]
            scale = transformationList[i][1]
            translation = transformationList[i][2]
            modelCoordinatesTransformed = createFieldsTransformations(modelCoordinates, rotation, scale, translation)[0]
            fieldassignment = modelCoordinates.createFieldassignment(modelCoordinatesTransformed)
            fieldassignment.assign()

            align.run()
            nodeset = fieldmodule.findNodesetByFieldDomainType(Field.DOMAIN_TYPE_NODES)

            for nodeIdentifier in range(1, 9):
                node = nodeset.findNodeByIdentifier(nodeIdentifier)
                fieldcache.setNode(node)
                result, x = modelCoordinates.getNodeParameters(fieldcache, -1, Node.VALUE_LABEL_VALUE, 1, 3)
                assertAlmostEqualList(self, x, expectedAlignedNodes[nodeIdentifier - 1], delta=1.0E-3)

    def test_modelFitGroupMarkers(self):
        """
        Test fitting with model fit group properly moves markers on boundary, and ignores markers outside.
        File two_cubes_hermite_nocross_groups.exf now has 3 marker points: outside, boundary and inside for this.
        """
        zinc_model_file = os.path.join(here, "resources", "two_cubes_hermite_nocross_groups.exf")
        zinc_data_file = os.path.join(here, "resources", "two_cubes_ellipsoid_data_regular_markers.exf")
        fitter = Fitter(zinc_model_file, zinc_data_file)
        fitter.setDiagnosticLevel(1)
        fitter.load()

        fieldmodule = fitter.getFieldmodule()
        mesh = fitter.getHighestDimensionMesh()
        self.assertEqual(2, mesh.getSize())
        element1 = mesh.findElementByIdentifier(1)
        self.assertTrue(element1.isValid())
        element2 = mesh.findElementByIdentifier(2)
        self.assertTrue(element2.isValid())
        groupTwo = fieldmodule.findFieldByName("two").castGroup()
        self.assertTrue(groupTwo.isValid())

        markerGroup = fitter.getMarkerGroup()
        self.assertTrue(markerGroup.isValid())
        markerDataGroup, markerDataCoordinates, markerDataName = fitter.getMarkerDataFields()
        dataHostLocation = fitter.getDataHostLocationField()
        activeDataNodesetGroup = fitter.getActiveDataNodesetGroup()

        fieldcache = fieldmodule.createFieldcache()
        TOL = 1.0E-12

        expectedMarkerDataLocations = {
            "outside": (element1, [0.5, 0.5, 0.5]),
            "boundary": (element1, [1.0, 0.5, 0.5]),
            "inside": (element2, [0.5, 0.5, 0.5]),
        }
        expectedMarkerDataLocationsModelFitGroup = {
            "outside": (None, None),
            "boundary": (element2, [0.0, 0.5, 0.5]),
            "inside": (element2, [0.5, 0.5, 0.5]),
        }

        for i in range(3):

            expectedLocations = expectedMarkerDataLocations
            if i == 1:
                fitter.setModelFitGroup(groupTwo)
                expectedLocations = expectedMarkerDataLocationsModelFitGroup
            elif i == 2:
                fitter.setModelFitGroup(None)  # test changing back to whole mesh
            markerDataLocationNodesetGroup = fitter.getMarkerDataLocationNodesetGroup()  # as recreated each time

            for name, expectedLocation in expectedLocations.items():
                markerNode = find_node_with_name(markerDataGroup, markerDataName, name)
                fieldcache.setNode(markerNode)
                element, xi = dataHostLocation.evaluateMeshLocation(fieldcache, 3)
                if not expectedLocation[0]:
                    self.assertFalse(element.isValid())
                    self.assertFalse(markerDataLocationNodesetGroup.containsNode(markerNode))
                    self.assertFalse(activeDataNodesetGroup.containsNode(markerNode))
                    continue
                self.assertTrue(element.isValid())
                self.assertTrue(markerDataLocationNodesetGroup.containsNode(markerNode))
                self.assertTrue(activeDataNodesetGroup.containsNode(markerNode))
                self.assertEqual(expectedLocation[0], element)
                self.assertAlmostEqual(expectedLocation[1][0], xi[0], delta=TOL)
                self.assertAlmostEqual(expectedLocation[1][1], xi[1], delta=TOL)
                self.assertAlmostEqual(expectedLocation[1][2], xi[2], delta=TOL)

    def test_nodeset_max_and_min(self):
        zinc_model_file = os.path.join(here, "resources", "two_element_cube.exf")

        context = Context("max_min")
        logger = context.getLogger()
        region = context.getDefaultRegion()
        region.readFile(zinc_model_file)
        data_region = context.createRegion()
        data_region.readFile(os.path.join(here, "resources", "cube_to_sphere_data_random.exf"))

        fm = region.getFieldmodule()

        buffer = write_to_buffer(data_region, resource_domain_type=Field.DOMAIN_TYPE_DATAPOINTS)
        result = read_from_buffer(region, buffer)
        nodes = fm.findNodesetByFieldDomainType(Field.DOMAIN_TYPE_DATAPOINTS)

        # Coordinate field 'data_coordinates' is defined on datapoints so nodeset evaluation should
        # result in something that is not None being returned.
        data_coordinates = fm.findFieldByName("data_coordinates")
        self.assertEqual(0, logger.getNumberOfMessages())
        min_range, max_range = evaluate_field_nodeset_range(data_coordinates, nodes)
        self.assertEqual(0, logger.getNumberOfMessages())
        self.assertIsNotNone(min_range)
        self.assertIsNotNone(max_range)

        # Coordinate field 'coordinates' is not defined on datapoints so nodeset evaluation should
        # result in None being returned.
        coordinates = fm.findFieldByName("coordinates")
        self.assertEqual(0, logger.getNumberOfMessages())
        min_range, max_range = evaluate_field_nodeset_range(coordinates, nodes)
        self.assertEqual(0, logger.getNumberOfMessages())
        self.assertIsNone(min_range)
        self.assertIsNone(max_range)


if __name__ == "__main__":
    unittest.main()
