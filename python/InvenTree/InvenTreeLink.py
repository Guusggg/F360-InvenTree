""" InveTree addin for Autodesk Fusion360 """
# Author-Matthias MAIR<mjmair DOT com>
# Description-use InvenTree-Inventory

import configparser
import json
import os
import sys
import traceback
from datetime import datetime

import adsk.cam
import adsk.core
import adsk.fusion

# Add in Modules Path
sys.path.insert(0, str(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Modules')))
from inventree.api import InvenTreeAPI
from inventree.base import Parameter, ParameterTemplate
from inventree.part import Part, PartCategory

# global variables for handling the addin-aspects
_APP = adsk.core.Application.cast(None)
_APP_UI = adsk.core.UserInterface.cast(None)
_APP_PANEL = None  # Holds reference for the newly added Panel
_APP_BTN_LST = []  # all added buttons for easy reference # debug
_APP_HANDLERS = []  # all handlers as per ADESK-recommandation

# globals to hold objects
BOM = []  # BOM-List
BOM_HIR = []  # Hirarchical BOM
INV_API = None  # API-connection
CONFIG = {}  # Config section
REF_CACHE = {}  # saves refs for reduced loading

# Magic numbers
PALETTE = 'InvenTreePalette'


# region functions
def config_get(ref):
    """ returns current config """
    # SET where config is saved here
    crt_srv = CONFIG['SERVER']['current']  # ref enables multiple server confs
    if ref == 'srv_address':
        return CONFIG[crt_srv]['address']
    if ref == 'srv_token':
        return CONFIG[crt_srv]['token']
    if ref == 'category':
        return CONFIG[crt_srv]['category']
    if ref == 'part_id':
        return CONFIG['SERVER']['part_id']
    raise NotImplementedError('unknown ref')


def config_ref(ref):
    """ retuns a (cached) api-object based on ref """
    def get(ref, cat):
        """ handles caching of ref-objects """
        global REF_CACHE
        if REF_CACHE.get(ref):
            return REF_CACHE.get(ref)

        REF_CACHE[ref] = [a for a in cat.list(inv_api()) if a.name == config_get(ref)][0]
        return REF_CACHE[ref]

    # set the API-objects
    if ref == 'category':
        return get(ref, PartCategory)
    if ref == 'part_id':
        return get(ref, ParameterTemplate)
    raise NotImplementedError('unknown ref')


def error(typ_str=None):
    """ shows message box when error raised """
    # generate error message
    if typ_str == 'cmd':
        ret_msg = 'Command executed failed: {}'.format(traceback.format_exc())
    else:
        ret_msg = 'Failed:\n{}'.format(traceback.format_exc())

    # show message
    if _APP_UI:
        _APP_UI.messageBox(ret_msg)
    else:
        print(ret_msg)


# Components
def _extract_bom():
    """ returns bom """
    try:
        design = _APP.activeProduct
        if not design:
            _APP_UI.messageBox('No active design', 'Extract BOM')
            return []

        # Get all occurrences in the root component of the active design
        occs = design.rootComponent.allOccurrences

        # Gather information about each unique component
        bom = []
        for occ in occs:
            comp = occ.component
            jj = 0
            for bomI in bom:
                if bomI['component'] == comp:
                    # Increment the instance count of the existing row.
                    bomI['instances'] += 1
                    break
                jj += 1

            if jj == len(bom):
                # Gather any BOM worthy values from the component
                volume = 0
                bodies = comp.bRepBodies
                for bodyK in bodies:
                    if bodyK.isSolid:
                        volume += bodyK.volume

                # Add this component to the BOM
                node = component_info(comp, comp_set=True)
                node['volume'] = volume
                node['linked'] = occ.isReferencedComponent
                bom.append(node)

        # Display the BOM
        return bom
    except Exception as _e:
        raise _e


def component_info(comp, parent='#', comp_set=False):
    """ returns a node element """
    node = {
        'name': comp.name,
        'nbr': comp.partNumber,
        'id': comp.id,
        'revision-id': comp.revisionId,
        'instances': 1,
        'parent': parent,
    }
    if comp_set:
        node['component'] = comp
    else:
        node['state'] = {'opened': True, 'checkbox_disabled': False}
        node["type"] = "4-root_component"
        node["text"] = comp.name
    return node


def make_component_tree():
    """ generates the full tree """
    root = _APP.activeProduct.rootComponent

    node_list = []

    root_node = component_info(root)
    root_node["type"] = "4-root_component"
    node_list.append(root_node)

    if root.occurrences.count > 0:
        make_assembly_nodes(root.occurrences, node_list, root.id)

    return node_list


def make_assembly_nodes(occurrences: adsk.fusion.OccurrenceList, node_list, parent):
    """ adds one node and checks for others """
    for occurrence in occurrences:

        node = component_info(occurrence.component, parent)
        if occurrence.childOccurrences.count > 0:

            node["type"] = "4-component_group"
            node_list.append(node)
            make_assembly_nodes(occurrence.childOccurrences, node_list, occurrence.component.id)

        else:
            node["type"] = "4-component"
            node_list.append(node)


# API
def inv_api():
    """ connect to API """
    global INV_API
    if not INV_API:
        INV_API = InvenTreeAPI(config_get('srv_address'), token=config_get('srv_token'))
        return INV_API
    return INV_API


def inventree_get_part(part_id):
    """ returns a part from InvenTree """
    def search(parameters, part_id):
        try:
            part = [a.part for a in parameters if a._data['data'] == part_id]
            if len(part) == 1:
                return Part(inv_api(), part[0])
            return False
        except Exception as _e:
            raise Exception from _e

    parameters = Parameter.list(inv_api())
    if not parameters:
        parameters = []
    if type(part_id) in (list, tuple):
        result = {}
        for cur_id in part_id:
            result[cur_id] = search(parameters, cur_id)
        return result
    return search(parameters, part_id)
# endregion


# region handlers
# Event handler for the commandExecuted event.
class ShowPaletteCommandExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        """ generic function - called when handler called """
        try:
            # Create and display the palette.
            palette = _APP_UI.palettes.itemById(PALETTE)
            if not palette:
                palette = _APP_UI.palettes.add(PALETTE, 'InvenTreeLink', 'palette.html', True, True, True, 300, 200)

                # Dock the palette to the right side of Fusion window.
                palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight

                # Add handler to HTMLEvent of the palette.
                onHTMLEvent = HTMLEventHandler()
                palette.incomingFromHTML.add(onHTMLEvent)
                _APP_HANDLERS.append(onHTMLEvent)

                # Add handler to CloseEvent of the palette.
                onClosed = MyCloseEventHandler()
                palette.closed.add(onClosed)
                _APP_HANDLERS.append(onClosed)
            else:
                palette.isVisible = True
        except Exception:
            error('cmd')


# Event handler for the commandCreated event.
class ShowPaletteCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        """ generic function - called when handler called """
        try:
            command = args.command
            onExecute = ShowPaletteCommandExecuteHandler()
            command.execute.add(onExecute)
            _APP_HANDLERS.append(onExecute)
        except Exception:
            error()


# Event handler for the commandExecuted event.
class SendBomCommandExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        """ generic function - called when handler called """
        try:
            palette = _APP_UI.palettes.itemById(PALETTE)
            if palette:
                palette.sendInfoToHTML('sendBom', '<br><br><br><div class="d-flex justify-content-center"><div class="spinner-border" role="status"><span class="visually-hidden">Loading...</span></div></div>')

                global BOM, BOM_HIR
                start = datetime.now()
                BOM = _extract_bom()
                BOM_HIR = make_component_tree()
                body = ''.join(['<tr><td>%s</td><td>%s</td></tr>' % (a['name'], a['instances']) for a in BOM])
                table_c = '<div class="overflow-auto"><table class="table table-sm table-striped table-hover"><thead><tr><th scope="col">Name</th><th scope="col">Count</th></tr></thead><tbody>{body}</tbody></table></div>'.format(body=body)

                palette.sendInfoToHTML('sendBom', '<p>{nbr} Stücke gefunden in {time}</p>{table}'.format(nbr=len(BOM), table=table_c, time=datetime.now() - start))
                palette.sendInfoToHTML('sendTree', json.dumps(BOM_HIR))
        except Exception:
            error('cmd')


# Event handler for the commandCreated event.
class SendBomCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        """ generic function - called when handler called """
        try:
            command = args.command
            onExecute = SendBomCommandExecuteHandler()
            command.execute.add(onExecute)
            _APP_HANDLERS.append(onExecute)
        except Exception:
            error()


# Event handler for the commandExecuted event.
class SendBomOnlineCommandExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        """ generic function - called when handler called """
        try:
            palette = _APP_UI.palettes.itemById(PALETTE)
            if palette:
                palette.sendInfoToHTML('sendBom', '<br><br><br><div class="d-flex justify-content-center"><div class="spinner-border" role="status"><span class="visually-hidden">Loading...</span></div></div>')

                # Work with it
                global BOM
                inv_status = inventree_get_part([a['id'] for a in BOM])
                for a in BOM:
                    a['status'] = inv_status[a['id']]

                body = ''.join(['<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (a['name'], a['instances'], a['status']) for a in BOM])
                table = '<div class="overflow-auto"><table class="table table-sm table-striped table-hover"><thead><tr><th scope="col">Name</th><th scope="col">Count</th><th scope="col">Status</th></tr></thead><tbody>{body}</tbody></table></div>'.format(body=body)

                palette.sendInfoToHTML('sendBom', '{table}'.format(table=table))
        except Exception:
            error('cmd')


# Event handler for the commandCreated event.
class SendBomOnlineCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        """ generic function - called when handler called """
        try:
            command = args.command
            onExecute = SendBomOnlineCommandExecuteHandler()
            command.execute.add(onExecute)
            _APP_HANDLERS.append(onExecute)
        except Exception:
            error()


# Event handler for the commandExecuted event.
class SendShowPartCommandExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        """ generic function - called when handler called """
        try:
            print('clicked ok')
            print(args)
        except Exception:
            error('cmd')


class ShowPartChangedHandler(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        """ generic function - called when handler called """
        try:
            eventArgs = adsk.core.InputChangedEventArgs.cast(args)
            if _APP_UI.activeSelections.count == 1:
                occ = adsk.fusion.Occurrence.cast(_APP_UI.activeSelections[0].entity)
                arg_id = eventArgs.input.id
                inp = eventArgs.inputs.command.commandInputs

                # Check to see what button was used
                if arg_id in ('partSelection', 'button_refresh'):
                    self.part_refresh(occ, inp, inventree_get_part(occ.component.id))
                elif arg_id == 'button_create':
                    # make part
                    part = self.part_create(occ, config_ref('category'), config_ref('part_id'))
                    # refresh display
                    self.part_refresh(occ, inp, Part(inv_api(), part.pk))
                else:
                    print('not found')
        except Exception:
            error()

    def part_create(self, occ, cat, para_cat):
        """ create part based on occurence """
        # create part itself
        part = Part.create(inv_api(), {
            'name': occ.component.name,
            'description': occ.component.description if occ.component.description else 'None',
            'IPN': occ.component.partNumber,
            'category': cat.pk,
            'active': True,
            'virtual': False,
        })
        # create the reference parameter
        Parameter.create(inv_api(), {'part': part.pk, 'template': para_cat.pk, 'data': occ.component.id})
        return part

    def part_refresh(self, occ, inp, part):
        """ updates PartInfo command-inputs with values for supplied parts """
        unitsMgr = _APP.activeDocument.design.unitsManager
        # Compnent Infos
        inp.itemById('text_id').text = occ.component.id
        inp.itemById('text_name').text = occ.component.name
        inp.itemById('text_description').text = occ.component.description
        inp.itemById('text_opacity').text = str(occ.component.opacity)
        inp.itemById('text_partNumber').text = occ.component.partNumber

        # Physics
        inp.itemById('text_area').text = '%.3f cm2' % float(unitsMgr.formatInternalValue(occ.physicalProperties.area, '', True))
        inp.itemById('text_volume').text = '%.3f cm3' % float(unitsMgr.formatInternalValue(occ.physicalProperties.volume, '', True))
        inp.itemById('text_mass').text = '%.3f g' % float(unitsMgr.formatInternalValue(occ.physicalProperties.mass, 'g', False))
        inp.itemById('text_density').text = '%.3f g/cm3' % float(unitsMgr.formatInternalValue(occ.physicalProperties.density, 'g/cm/cm/cm', False))
        inp.itemById('text_material').text = occ.component.material.name if occ.component.material else ''

        # bounding box
        axis = ['x', 'y', 'z']
        bb_min = {a: getattr(occ.boundingBox.minPoint, a) for a in axis}
        bb_max = {a: getattr(occ.boundingBox.maxPoint, a) for a in axis}
        bb = {a: bb_max[a] - bb_min[a] for a in axis}

        tableInput = inp.itemById('table')
        tableInput.clear()
        tbl_cmds = tableInput.commandInputs
        tbl_val = [bb, bb_min, bb_max]
        tbl_col = ['bound', 'bound_min', 'bound_max']
        for i in range(3):
            row = tableInput.rowCount
            for ii in range(4):
                if ii == 0:
                    val = tbl_col[i]
                else:
                    val = '%s: %.3f' % (axis[ii - 1], tbl_val[i][axis[ii - 1]])
                ref = '%s_%s' % (i, ii)
                txtinp = tbl_cmds.addTextBoxCommandInput('table_' + ref, ref, val, 1, True)
                tableInput.addCommandInput(txtinp, row, ii)

        # InvenTree part
        if part:
            if part.thumbnail:  # TODO implement images
                pass
            #     inp.itemById('text_part_image').imageFile = part.thumbnail
            #     inp.itemById('text_part_image').isVisible = True
            inp.itemById('text_part_name').text = part.name
            inp.itemById('text_part_ipn').text = part.IPN if part.IPN else ''
            inp.itemById('text_part_description').text = part.description if part.description else ''
            inp.itemById('text_part_notes').text = part.notes if part.notes else ''
            inp.itemById('text_part_keywords').text = part.keywords if part.keywords else ''
            inp.itemById('text_part_category').text = part.getCategory().pathstring
            inp.itemById('text_part_stock').text = str(part.in_stock)
            inp.itemById('bool_part_virtual').value = part.virtual
            inp.itemById('bool_part_template').value = part.is_template
            inp.itemById('bool_part_assembly').value = part.assembly
            inp.itemById('bool_part_component').value = part.component
            inp.itemById('bool_part_trackable').value = part.trackable
            inp.itemById('bool_part_purchaseable').value = part.purchaseable
            inp.itemById('bool_part_salable').value = part.salable
            inp.itemById('text_part_bom').text = str(part.name)
            inp.itemById('text_part_suppliers').text = str(part.suppliers)
            message = '<div align="center">open <b>part %s</b> in <b>%s</b> <a href="%s">with this link</a>.</div>' % (part.pk, inv_api().server_details['instance'], inv_api().base_url[:-4] + part._url)
            inp.itemById('text_part_link').formattedText = message
            if part.link:
                inp.itemById('text_part_link_ext').formattedText = '<a href="%s">external link</a>' % part.link
            else:
                inp.itemById('text_part_link_ext').isVisible = False

        # Control visibility of Groups
        inp.itemById('grp_1').isVisible = bool(part)
        inp.itemById('grp_2').isVisible = bool(part)
        inp.itemById('grp_3').isVisible = bool(part)
        inp.itemById('button_create').isVisible = not bool(part)


# Event handler for the commandCreated event.
class SendShowPartCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        """ generic function - called when handler called """
        try:
            command = args.command
            onExecute = SendShowPartCommandExecuteHandler()
            command.execute.add(onExecute)
            _APP_HANDLERS.append(onExecute)

            onInputChanged = ShowPartChangedHandler()
            command.inputChanged.add(onInputChanged)
            _APP_HANDLERS.append(onInputChanged)

            inputs = command.commandInputs

            # Tabs
            tabCmdInput1 = inputs.addTabCommandInput('tab_1', 'Start')
            tab1ChildInputs = tabCmdInput1.children
            tabCmdInput2 = inputs.addTabCommandInput('tab_2', 'Teil-Details')
            tab2ChildInputs = tabCmdInput2.children

            # TextInputs for general information
            tab2ChildInputs.addTextBoxCommandInput('text_id', 'id', 'id', 1, True)
            tab2ChildInputs.addTextBoxCommandInput('text_name', 'name', 'name', 1, True)
            tab2ChildInputs.addTextBoxCommandInput('text_description', 'description', 'description', 1, True)
            tab2ChildInputs.addTextBoxCommandInput('text_opacity', 'opacity', 'opacity', 1, True)
            tab2ChildInputs.addTextBoxCommandInput('text_partNumber', 'partNumber', 'partNumber', 1, True)
            tab2ChildInputs.addTextBoxCommandInput('text_area', 'area', 'area', 1, True)
            tab2ChildInputs.addTextBoxCommandInput('text_volume', 'volume', 'volume', 1, True)
            tab2ChildInputs.addTextBoxCommandInput('text_mass', 'mass', 'mass', 1, True)
            tab2ChildInputs.addTextBoxCommandInput('text_density', 'density', 'density', 1, True)
            tab2ChildInputs.addTextBoxCommandInput('text_material', 'material', 'material', 1, True)
            tableInput = tab2ChildInputs.addTableCommandInput('table', 'Table', 4, '1:1:1:1')
            tableInput.isFullWidth = True
            tableInput.tablePresentationStyle = 2

            # Select
            selectInput = tab1ChildInputs.addSelectionInput('partSelection', 'Select', 'Please select components')
            selectInput.addSelectionFilter('Occurrences')
            selectInput.setSelectionLimits(1, 1)
            # Buttons
            tab1ChildInputs.addBoolValueInput('button_create', 'create part', False, 'resources/ButtonCreate', True)
            tab1ChildInputs.addBoolValueInput('button_refresh', 'refresh Information', False, 'resources/SendOnlineState', True)

            # TextInputs for InvenTree
            grpCmdInput1 = tab1ChildInputs.addGroupCommandInput('grp_1', 'General')
            grp1ChildInputs = grpCmdInput1.children
            img = tab1ChildInputs.addImageCommandInput('text_part_image', 'image', 'resources/blank_image.png')
            img.isVisible = False
            grp1ChildInputs.addTextBoxCommandInput('text_part_name', 'name', 'name', 1, False)
            grp1ChildInputs.addTextBoxCommandInput('text_part_ipn', 'IPN', 'IPN', 1, False)
            grp1ChildInputs.addTextBoxCommandInput('text_part_description', 'description', 'description', 1, False)
            grp1ChildInputs.addTextBoxCommandInput('text_part_notes', 'note', 'note', 2, False)
            grp1ChildInputs.addTextBoxCommandInput('text_part_keywords', 'keywords', 'keywords', 1, False)
            grp1ChildInputs.addTextBoxCommandInput('text_part_category', 'category', 'category', 1, True)
            grp1ChildInputs.addTextBoxCommandInput('text_part_link', '', 'linktext', 1, True)

            grpCmdInput2 = tab1ChildInputs.addGroupCommandInput('grp_2', 'Settings')
            grp2ChildInputs = grpCmdInput2.children
            grp2ChildInputs.addBoolValueInput('bool_part_virtual', 'virtual', True)
            grp2ChildInputs.addBoolValueInput('bool_part_template', 'template', True)
            grp2ChildInputs.addBoolValueInput('bool_part_assembly', 'assembly', True)
            grp2ChildInputs.addBoolValueInput('bool_part_component', 'component', True)
            grp2ChildInputs.addBoolValueInput('bool_part_trackable', 'trackable', True)
            grp2ChildInputs.addBoolValueInput('bool_part_purchaseable', 'purchaseable', True)
            grp2ChildInputs.addBoolValueInput('bool_part_salable', 'salable', True)

            grpCmdInput3 = tab1ChildInputs.addGroupCommandInput('grp_3', 'Supply')
            grp3ChildInputs = grpCmdInput3.children
            grp3ChildInputs.addTextBoxCommandInput('text_part_stock', 'stock', 'stock', 1, True)
            grp3ChildInputs.addTextBoxCommandInput('text_part_bom', 'BOM items', 'Bom items', 1, True)
            grp3ChildInputs.addTextBoxCommandInput('text_part_suppliers', 'suppliers', 'suppliers', 1, True)
            grp3ChildInputs.addTextBoxCommandInput('text_part_link_ext', 'link', '', 1, True)

            # Turn off everything InvenTree
            inputs.itemById('grp_1').isVisible = False
            inputs.itemById('grp_2').isVisible = False
            inputs.itemById('grp_3').isVisible = False
        except Exception:
            error()


# Event handler for the palette close event.
class MyCloseEventHandler(adsk.core.UserInterfaceGeneralEventHandler):
    def notify(self, args):
        """ generic function - called when handler called """
        try:
            pass  # TODO cleanup function needed?
        except Exception:
            error()


# Event handler for the palette HTML event.
class HTMLEventHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        """ generic function - called when handler called """
        try:
            htmlArgs = adsk.core.HTMLEventArgs.cast(args)
            data = json.loads(htmlArgs.data)

            palette = _APP_UI.palettes.itemById(PALETTE)
            if htmlArgs.action == 'getBom':
                if palette:
                    _APP_UI.commandDefinitions.itemById('SendBom').execute()

            elif htmlArgs.action == 'getBomOnline':
                if palette:
                    _APP_UI.commandDefinitions.itemById('SendOnlineState').execute()

            elif htmlArgs.action == 'showPart':
                selections = _APP_UI.activeSelections
                selections.clear()

                design = _APP.activeDocument.design
                cmp = _APP.activeProduct.allComponents.itemById(data['id'])
                # occ = design.rootComponent.allOccurrencesByComponent(cmp)
                # bb = [a for a in design.rootComponent.allOccurrences if a.name == cmp.name]
                token = cmp.entityToken
                entitiesByToken = design.findEntityByToken(token)
                selections.add(entitiesByToken)  # TODO selection not working
                print(data['id'])
                _APP_UI.commandDefinitions.itemById('SendPart').execute()

            else:
                raise NotImplementedError('unknown message received from HTML')
        except Exception:
            error()
# endregion


def run(context):
    """ generic function - called when addin starts up """
    try:
        global _APP_UI, _APP, _APP_PANEL, _APP_BTN_LST
        _APP = adsk.core.Application.get()
        _APP_UI = _APP.userInterface

        # Make UI
        workSpace = _APP_UI.workspaces.itemById('FusionSolidEnvironment')
        tbPanels = workSpace.toolbarPanels
        _APP_PANEL = tbPanels.itemById('InvenTreeLink')
        if _APP_PANEL:
            _APP_PANEL.deleteMe()
        _APP_PANEL = tbPanels.add('InvenTreeLink', 'InvenTree - Link', 'SelectPanel', False)

        # Add a command that displays the panel.
        showPaletteCmdDef = _APP_UI.commandDefinitions.itemById('ShowPalette')
        if not showPaletteCmdDef:
            showPaletteCmdDef = _APP_UI.commandDefinitions.addButtonDefinition('ShowPalette', 'Show Palette', 'Show the palette for the BOM', 'resources\\ShowPalette')

            # Connect to Command Created event.
            onCommandCreated = ShowPaletteCommandCreatedHandler()
            showPaletteCmdDef.commandCreated.add(onCommandCreated)
            _APP_HANDLERS.append(onCommandCreated)
            _APP_BTN_LST.append(['ShowPalette', showPaletteCmdDef])

        SendBomCmdDef = _APP_UI.commandDefinitions.itemById('SendBom')
        if not SendBomCmdDef:
            SendBomCmdDef = _APP_UI.commandDefinitions.addButtonDefinition('SendBom', 'get BOM', 'Send BOM-Info to Palette HTML', 'resources\\SendBom')
            SendBomCmdDef.isPromotedByDefault = True
            SendBomCmdDef.isPromoted = True

            # Connect to Command Created event.
            onCommandCreated = SendBomCommandCreatedHandler()
            SendBomCmdDef.commandCreated.add(onCommandCreated)
            _APP_HANDLERS.append(onCommandCreated)
            _APP_BTN_LST.append(['SendBom', SendBomCmdDef])

        SendBomOnlineCmdDef = _APP_UI.commandDefinitions.itemById('SendOnlineState')
        if not SendBomOnlineCmdDef:
            SendBomOnlineCmdDef = _APP_UI.commandDefinitions.addButtonDefinition('SendOnlineState', 'get online status', 'gets the online status for all BOM-parts', 'resources\\SendOnlineState')

            # Connect to Command Created event.
            onCommandCreated = SendBomOnlineCommandCreatedHandler()
            SendBomOnlineCmdDef.commandCreated.add(onCommandCreated)
            _APP_HANDLERS.append(onCommandCreated)
            _APP_BTN_LST.append(['SendOnlineState', SendBomOnlineCmdDef])

        SendShowPartCmdDef = _APP_UI.commandDefinitions.itemById('SendPart')
        if not SendShowPartCmdDef:
            SendShowPartCmdDef = _APP_UI.commandDefinitions.addButtonDefinition('SendPart', 'get online status', 'gets the online status for all BOM-parts', 'resources\\SendPart')
            SendShowPartCmdDef.isPromotedByDefault = True
            SendShowPartCmdDef.isPromoted = True

            # Connect to Command Created event.
            onCommandCreated = SendShowPartCommandCreatedHandler()
            SendShowPartCmdDef.commandCreated.add(onCommandCreated)
            _APP_HANDLERS.append(onCommandCreated)
            _APP_BTN_LST.append(['SendPart', SendShowPartCmdDef])

        # Add the command to the toolbar.
        for btn in _APP_BTN_LST:
            cntrl = _APP_PANEL.controls.itemById(btn[0])
            if not cntrl:
                _APP_PANEL.controls.addCommand(btn[1])

        # Load settings
        global CONFIG
        config = configparser.ConfigParser()
        config.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'conf.ini'))
        CONFIG = config
        # with open('conf.ini', 'w') as configfile:
        #     config.write(configfile)
    except Exception:
        error()


def stop(context):
    """ generic function - called when addin stopped """
    global _APP_BTN_LST
    try:
        # Delete the palette created by this add-in.
        palette = _APP_UI.palettes.itemById(PALETTE)
        if palette:
            palette.deleteMe()

        for btn in _APP_BTN_LST:
            cntrl = _APP_PANEL.controls.itemById(btn[0])
            if cntrl:
                cntrl.deleteMe()
                _APP_BTN_LST.remove(btn)

        if _APP_PANEL:
            _APP_PANEL.deleteMe()
    except Exception:
        error()
