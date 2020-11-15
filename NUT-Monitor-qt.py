#!/usr/bin/env python
# -*- coding: utf-8 -*-

# 2009-12-27 David Goncalves - Version 1.2
#            Total rewrite of NUT-Monitor to optimize GUI interaction.
#            Added favorites support (saved to user's home)
#            Added status icon on the notification area
#
# 2010-02-26 David Goncalves
#            Added UPS vars display and the possibility to change values
#            when user double-clicks on a RW var.
#
# 2010-05-01 David Goncalves
#            Added support for PyNotify (if available)
#
# 2010-05-05 David Goncalves
#            Added support for command line options
#            -> --start-hidden
#            -> --favorite
#
#            NUT-Monitor now tries to detect if there is a NUT server
#            on localhost and if there is 1 UPS, connects to it.
#
# 2010-10-06 David Goncalves - Version 1.3
#            Added localisation support
#
# 2015-02-14 Michal Fincham - Version 1.3.1
#            Corrected unsafe permissions on ~/.nut-monitor (Debian #777706)


import gtk, gtk.glade, gobject
import sys
import base64
import os, os.path
import stat
import platform
import time
import threading
import optparse
import ConfigParser
import locale
import gettext
import PyNUT


# Activate threadings on glib
gobject.threads_init()

class interface :

    DESIRED_FAVORITES_DIRECTORY_MODE = 0700

    __widgets                        = {}
    __callbacks                      = {}
    __favorites                      = {}
    __favorites_file                 = None
    __favorites_path                 = ""
    __fav_menu_items                 = list()
    __window_visible                 = True
    __glade_file                     = None
    __connected                      = False
    __ups_handler                    = None
    __ups_commands                   = None
    __ups_vars                       = None
    __ups_rw_vars                    = None
    __gui_thread                     = None
    __current_ups                    = None

    def __init__( self ) :

        # Before anything, parse command line options if any present...
        opt_parser = optparse.OptionParser()
        opt_parser.add_option( "-H", "--start-hidden", action="store_true", default=False, dest="hidden", help="Start iconified in tray" )
        opt_parser.add_option( "-F", "--favorite", dest="favorite", help="Load the specified favorite and connect to UPS" )

        ( cmd_opts, args ) = opt_parser.parse_args()


        self.__glade_file = os.path.join( os.path.dirname( sys.argv[0] ), "gui-1.3.glade" )

        self.__widgets["interface"]                   = gtk.glade.XML( self.__glade_file, "window1", APP )
        self.__widgets["main_window"]                 = self.__widgets["interface"].get_widget("window1")
        self.__widgets["status_bar"]                  = self.__widgets["interface"].get_widget("statusbar2")
        self.__widgets["ups_host_entry"]              = self.__widgets["interface"].get_widget("entry1")
        self.__widgets["ups_port_entry"]              = self.__widgets["interface"].get_widget("spinbutton1")
        self.__widgets["ups_refresh_button"]          = self.__widgets["interface"].get_widget("button1")
        self.__widgets["ups_authentication_check"]    = self.__widgets["interface"].get_widget("checkbutton1")
        self.__widgets["ups_authentication_frame"]    = self.__widgets["interface"].get_widget("hbox1")
        self.__widgets["ups_authentication_login"]    = self.__widgets["interface"].get_widget("entry2")
        self.__widgets["ups_authentication_password"] = self.__widgets["interface"].get_widget("entry3")
        self.__widgets["ups_list_combo"]              = self.__widgets["interface"].get_widget("combobox1")
        self.__widgets["ups_commands_button"]         = self.__widgets["interface"].get_widget("button8")
        self.__widgets["ups_connect"]                 = self.__widgets["interface"].get_widget("button2")
        self.__widgets["ups_disconnect"]              = self.__widgets["interface"].get_widget("button7")
        self.__widgets["ups_params_box"]              = self.__widgets["interface"].get_widget("vbox6")
        self.__widgets["ups_infos"]                   = self.__widgets["interface"].get_widget("notebook1")
        self.__widgets["ups_vars_tree"]               = self.__widgets["interface"].get_widget("treeview1")
        self.__widgets["ups_vars_refresh"]            = self.__widgets["interface"].get_widget("button9")
        self.__widgets["ups_status_image"]            = self.__widgets["interface"].get_widget("image1")
        self.__widgets["ups_status_left"]             = self.__widgets["interface"].get_widget("label10")
        self.__widgets["ups_status_right"]            = self.__widgets["interface"].get_widget("label11")
        self.__widgets["ups_status_time"]             = self.__widgets["interface"].get_widget("label15")
        self.__widgets["menu_favorites_root"]         = self.__widgets["interface"].get_widget("menuitem3")
        self.__widgets["menu_favorites"]              = self.__widgets["interface"].get_widget("menu2")
        self.__widgets["menu_favorites_add"]          = self.__widgets["interface"].get_widget("menuitem4")
        self.__widgets["menu_favorites_del"]          = self.__widgets["interface"].get_widget("menuitem5")
        self.__widgets["progress_battery_charge"]     = self.__widgets["interface"].get_widget("progressbar1")
        self.__widgets["progress_battery_load"]       = self.__widgets["interface"].get_widget("progressbar2")

        # Create the tray icon and connect it to the show/hide method...
        self.__widgets["status_icon"] = gtk.StatusIcon()
        self.__widgets["status_icon"].set_from_file( os.path.join( os.path.dirname( sys.argv[0] ), "pixmaps", "on_line.png" ) )
        self.__widgets["status_icon"].set_visible( True )
        self.__widgets["status_icon"].connect( "activate", self.tray_activated )

        self.__widgets["ups_status_image"].set_from_file( os.path.join( os.path.dirname( sys.argv[0] ), "pixmaps", "on_line.png" ) )

        # Define interface callbacks actions
        self.__callbacks = { "on_window1_destroy"              : self.quit,
                             "on_imagemenuitem1_activate"      : self.gui_about_dialog,
                             "on_imagemenuitem5_activate"      : self.quit,
                             "on_entry1_changed"               : self.__check_gui_fields,
                             "on_entry2_changed"               : self.__check_gui_fields,
                             "on_entry3_changed"               : self.__check_gui_fields,
                             "on_checkbutton1_toggled"         : self.__check_gui_fields,
                             "on_spinbutton1_value_changed"    : self.__check_gui_fields,
                             "on_button1_clicked"              : self.__update_ups_list,
                             "on_button2_clicked"              : self.connect_to_ups,
                             "on_button7_clicked"              : self.disconnect_from_ups,
                             "on_button9_clicked"              : self.__gui_update_ups_vars_view,
                             "on_menuitem4_activate"           : self.__gui_add_favorite,
                             "on_menuitem5_activate"           : self.__gui_delete_favorite,
                             "on_treeview1_button_press_event" : self.__gui_ups_vars_selected
                           }

        # Connect the callbacks
        self.__widgets["interface"].signal_autoconnect( self.__callbacks )

        # Remove the dummy combobox entry on UPS List and Commands
        self.__widgets["ups_list_combo"].remove_text( 0 )

        # Set UPS vars treeview properties -----------------------------
        store = gtk.ListStore( gtk.gdk.Pixbuf, gobject.TYPE_STRING, gobject.TYPE_STRING )
        self.__widgets["ups_vars_tree"].set_model( store )
        self.__widgets["ups_vars_tree"].set_headers_visible( True )

        # Column 0
        cr = gtk.CellRendererPixbuf()
        column = gtk.TreeViewColumn( '', cr )
        column.add_attribute( cr, 'pixbuf', 0 )
        self.__widgets["ups_vars_tree"].append_column( column )

        # Column 1
        cr = gtk.CellRendererText()
        cr.set_property( 'editable', False )
        column = gtk.TreeViewColumn( _('Var name'), cr )
        column.set_sort_column_id( 1 )
        column.add_attribute( cr, 'text', 1 )
        self.__widgets["ups_vars_tree"].append_column( column )

        # Column 2
        cr = gtk.CellRendererText()
        cr.set_property( 'editable', False )
        column = gtk.TreeViewColumn( _('Value'), cr )
        column.add_attribute( cr, 'text', 2 )
        self.__widgets["ups_vars_tree"].append_column( column )

        self.__widgets["ups_vars_tree"].get_model().set_sort_column_id( 1, gtk.SORT_ASCENDING )
        self.__widgets["ups_vars_tree_store"] = store

        self.__widgets["ups_vars_tree"].set_size_request( -1, 50 )
        #---------------------------------------------------------------

        # UPS Commands combo box creation ------------------------------
        container = self.__widgets["ups_commands_button"].get_parent()
        self.__widgets["ups_commands_button"].destroy()
        self.__widgets["ups_commands_combo"] = gtk.ComboBox()

        list_store = gtk.ListStore( gobject.TYPE_STRING )

        self.__widgets["ups_commands_combo"].set_model( list_store )
        cell_renderer = gtk.CellRendererText()
        cell_renderer.set_property( "xalign", 0 )
        self.__widgets["ups_commands_combo"].pack_start( cell_renderer, True )
        self.__widgets["ups_commands_combo"].add_attribute( cell_renderer, "markup", 0 )

        container.pack_start( self.__widgets["ups_commands_combo"], True )
        self.__widgets["ups_commands_combo"].set_active( 0 )
        self.__widgets["ups_commands_combo"].show_all()

        self.__widgets["ups_commands_button"] = gtk.Button( stock=gtk.STOCK_EXECUTE )
        container.pack_start( self.__widgets["ups_commands_button"], True )
        self.__widgets["ups_commands_button"].show()
        self.__widgets["ups_commands_button"].connect( "clicked", self.__gui_send_ups_command )

        self.__widgets["ups_commands_combo_store"] = list_store
        #---------------------------------------------------------------

        if ( cmd_opts.hidden != True ) :
            self.__widgets["main_window"].show()

        # Define favorites path and load favorites
        if ( platform.system() == "Linux" ) :
            self.__favorites_path = os.path.join( os.environ.get("HOME"), ".nut-monitor" )
        elif ( platform.system() == "Windows" ) :
            self.__favorites_path = os.path.join( os.environ.get("USERPROFILE"), "Application Data", "NUT-Monitor" )

        self.__favorites_file = os.path.join( self.__favorites_path, "favorites.ini" )
        self.__parse_favorites()

        self.gui_status_message( _("Welcome to NUT Monitor") )

        if ( cmd_opts.favorite != None ) :
            if ( self.__favorites.has_key( cmd_opts.favorite ) ) :
                self.__gui_load_favorite( fav_name=cmd_opts.favorite )
                self.connect_to_ups()
        else :
            # Try to scan localhost for available ups and connect to it if there is only one
            self.__widgets["ups_host_entry"].set_text( "localhost" )
            self.__update_ups_list()
            if ( len( self.__widgets["ups_list_combo"].get_model() ) == 1 ) :
                self.connect_to_ups()

    # Check if correct fields are filled to enable connection to the UPS
    def __check_gui_fields( self, widget=None ) :
        # If UPS list contains something, clear it
        if self.__widgets["ups_list_combo"].get_active() != -1 :
            self.__widgets["ups_list_combo"].get_model().clear()
            self.__widgets["ups_connect"].set_sensitive( False )
            self.__widgets["menu_favorites_add"].set_sensitive( False )

        # Host/Port selection
        if len( self.__widgets["ups_host_entry"].get_text() ) > 0 :
            sensitive = True

            # If authentication is selected, check that we have a login and password
            if self.__widgets["ups_authentication_check"].get_active() :
                if len( self.__widgets["ups_authentication_login"].get_text() ) == 0 :
                    sensitive = False

                if len( self.__widgets["ups_authentication_password"].get_text() ) == 0 :
                    sensitive = False

            self.__widgets["ups_refresh_button"].set_sensitive( sensitive )
            if not sensitive :
                self.__widgets["ups_connect"].set_sensitive( False )
                self.__widgets["menu_favorites_add"].set_sensitive( False )
        else :
            self.__widgets["ups_refresh_button"].set_sensitive( False )
            self.__widgets["ups_connect"].set_sensitive( False )
            self.__widgets["menu_favorites_add"].set_sensitive( False )

        # Use authentication fields...
        if self.__widgets["ups_authentication_check"].get_active() :
            self.__widgets["ups_authentication_frame"].set_sensitive( True )
        else :
            self.__widgets["ups_authentication_frame"].set_sensitive( False )

        self.gui_status_message()

    #-------------------------------------------------------------------
    # This method is used to show/hide the main window when user clicks on the tray icon
    def tray_activated( self, widget=None, data=None ) :
        if self.__window_visible :
            self.__widgets["main_window"].hide()
        else :
            self.__widgets["main_window"].show()

        self.__window_visible = not self.__window_visible

    #-------------------------------------------------------------------
    # Change the status icon and tray icon
    def change_status_icon( self, icon="on_line", blink=False ) :
        self.__widgets["status_icon"].set_from_file( os.path.join( os.path.dirname( sys.argv[0] ), "pixmaps", "%s.png" % icon ) )
        self.__widgets["ups_status_image"].set_from_file( os.path.join( os.path.dirname( sys.argv[0] ), "pixmaps", "%s.png" % icon ) )
        self.__widgets["status_icon"].set_blinking( blink )

    #-------------------------------------------------------------------
    # This method connects to the NUT server and retrieve availables UPSes
    # using connection parameters (host, port, login, pass...)
    def __update_ups_list( self, widget=None ) :

        host     = self.__widgets["ups_host_entry"].get_text()
        port     = int( self.__widgets["ups_port_entry"].get_value() )
        login    = None
        password = None

        if self.__widgets["ups_authentication_check"].get_active() :
            login    = self.__widgets["ups_authentication_login"].get_text()
            password = self.__widgets["ups_authentication_password"].get_text()

        try :
            nut_handler = PyNUT.PyNUTClient( host=host, port=port, login=login, password=password )
            upses = nut_handler.GetUPSList()

            ups_list = upses.keys()
            ups_list.sort()

            # If UPS list contains something, clear it
            self.__widgets["ups_list_combo"].get_model().clear()

            for current in ups_list :
                self.__widgets["ups_list_combo"].append_text( current )

            self.__widgets["ups_list_combo"].set_active( 0 )

            self.__widgets["ups_connect"].set_sensitive( True )
            self.__widgets["menu_favorites_add"].set_sensitive( True )

            self.gui_status_message( _("Found {0} devices on {1}").format( len( ups_list ), host ) )

        except :
            error_msg = _("Error connecting to '{0}' ({1})").format( host, sys.exc_info()[1] )
            self.gui_status_message( error_msg )

    #-------------------------------------------------------------------
    # Quit program
    def quit( self, widget=None ) :
        # If we are connected to an UPS, disconnect first...
        if self.__connected :
            self.gui_status_message( _("Disconnecting from device") )
            self.disconnect_from_ups()

        gtk.main_quit()

    #-------------------------------------------------------------------
    # Method called when user wants to add a new favorite entry. It
    # displays a dialog to enable user to select the name of the favorite
    def __gui_add_favorite( self, widget=None ) :
        dialog_interface = gtk.glade.XML( self.__glade_file, "dialog1" )
        dialog = dialog_interface.get_widget( "dialog1" )

        self.__widgets["favorites_dialog_button_add"] = dialog_interface.get_widget("button3")

        # Define interface callbacks actions
        callbacks = { "on_entry4_changed" : self.__gui_add_favorite_check_gui_fields }
        dialog_interface.signal_autoconnect( callbacks )

        self.__widgets["main_window"].set_sensitive( False )
        rc = dialog.run()
        if rc == 1 :
            fav_data = {}
            fav_data["host"] = self.__widgets["ups_host_entry"].get_text()
            fav_data["port"] = "%d" % self.__widgets["ups_port_entry"].get_value()
            fav_data["ups"]  = self.__widgets["ups_list_combo"].get_active_text()
            fav_data["auth"] = self.__widgets["ups_authentication_check"].get_active()
            if fav_data["auth"] :
                fav_data["login"]    = self.__widgets["ups_authentication_login"].get_text()
                fav_data["password"] = base64.b64encode( self.__widgets["ups_authentication_password"].get_text() )

            fav_name = dialog_interface.get_widget("entry4").get_text()
            self.__favorites[ fav_name ] = fav_data
            self.__gui_refresh_favorites_menu()

            # Save all favorites
            self.__save_favorites()

        dialog.destroy()
        self.__widgets["main_window"].set_sensitive( True )

    #-------------------------------------------------------------------
    # Method called when user wants to delete an entry from favorites
    def __gui_delete_favorite( self, widget=None ) :

        dialog_interface = gtk.glade.XML( self.__glade_file, "dialog2" )
        dialog = dialog_interface.get_widget( "dialog2" )

        # Remove the dummy combobox entry on list
        dialog_interface.get_widget("combobox2").remove_text( 0 )

        favs = self.__favorites.keys()
        favs.sort()
        for current in favs :
            dialog_interface.get_widget("combobox2").append_text( current )

        dialog_interface.get_widget("combobox2").set_active( 0 )

        self.__widgets["main_window"].set_sensitive( False )
        rc = dialog.run()
        fav_name = dialog_interface.get_widget("combobox2").get_active_text()
        dialog.destroy()
        self.__widgets["main_window"].set_sensitive( True )

        if ( rc == 1 ) :
            # Remove entry, show confirmation dialog
            md = gtk.MessageDialog( None, gtk.DIALOG_MODAL, gtk.MESSAGE_QUESTION, gtk.BUTTONS_YES_NO, _("Are you sure that you want to remove this favorite ?") )
            resp = md.run()
            md.destroy()

            if ( resp == gtk.RESPONSE_YES ) :
                del self.__favorites[ fav_name ]
                self.__gui_refresh_favorites_menu()
                self.__save_favorites()
                self.gui_status_message( _("Removed favorite '%s'") % fav_name )

    #-------------------------------------------------------------------
    # Method called when user selects a favorite from the favorites menu
    def __gui_load_favorite( self, fav_name="" ) :

        if ( self.__favorites.has_key( fav_name ) ) :
            # If auth is activated, process it before other fields to avoir weird
            # reactions with the 'check_gui_fields' function.
            if ( self.__favorites[fav_name].get("auth", False ) ) :
                self.__widgets["ups_authentication_check"].set_active( True )
                self.__widgets["ups_authentication_login"].set_text( self.__favorites[fav_name].get("login","") )
                self.__widgets["ups_authentication_password"].set_text( self.__favorites[fav_name].get("password","") )

            self.__widgets["ups_host_entry"].set_text( self.__favorites[fav_name].get("host","") )
            self.__widgets["ups_port_entry"].set_value( float(self.__favorites[fav_name].get("port",3493.0)) )

            # Clear UPS list and add current UPS name
            self.__widgets["ups_list_combo"].get_model().clear()

            self.__widgets["ups_list_combo"].append_text( self.__favorites[fav_name].get("ups","") )
            self.__widgets["ups_list_combo"].set_active( 0 )

            # Activate the connect button
            self.__widgets["ups_connect"].set_sensitive( True )

            self.gui_status_message( _("Loaded '%s'") % fav_name )

    #-------------------------------------------------------------------
    # Send the selected command to the UPS
    def __gui_send_ups_command( self, widget=None ) :
        offset = self.__widgets["ups_commands_combo"].get_active()
        cmd    = self.__ups_commands[ offset ]

        md = gtk.MessageDialog( None, gtk.DIALOG_MODAL, gtk.MESSAGE_QUESTION, gtk.BUTTONS_YES_NO, _("Are you sure that you want to send\n'%s' to the device ?") % cmd )
        self.__widgets["main_window"].set_sensitive( False )
        resp = md.run()
        md.destroy()
        self.__widgets["main_window"].set_sensitive( True )

        if ( resp == gtk.RESPONSE_YES ) :
            try :
                self.__ups_handler.RunUPSCommand( self.__current_ups, cmd )
                self.gui_status_message( _("Sent '{0}' command to {1}").format( cmd, self.__current_ups ) )

            except :
                self.gui_status_message( _("Failed to send '{0}' ({1})").format( cmd, sys.exc_info()[1] ) )

    #-------------------------------------------------------------------
    # Method called when user clicks on the UPS vars treeview. If the user
    # performs a double click on a RW var, the GUI shows the update var dialog.
    def __gui_ups_vars_selected( self, widget, event ) :
        # Check if it's a double click...
        if ( (event.button == 1) and (event.type == gtk.gdk._2BUTTON_PRESS) ) :
            treeselection = self.__widgets["ups_vars_tree"].get_selection()
            (model,iter)  = treeselection.get_selected()
            try :
                ups_var = model.get_value( iter, 1 )
                if ( ups_var in self.__ups_rw_vars ) :
                    # The selected var is RW, then we can show the update dialog
                    dialog_interface = gtk.glade.XML( self.__glade_file, "dialog3" )
                    dialog = dialog_interface.get_widget( "dialog3" )

                    lab = dialog_interface.get_widget( "label9" )
                    lab.set_markup( _("Enter a new value for the variable.\n\n{0} = {1} <span color=\"#606060\"><i>(current value)</i></span>").format( ups_var, self.__ups_rw_vars.get(ups_var)) )

                    str = dialog_interface.get_widget( "entry5" )
                    str.set_text( self.__ups_rw_vars.get(ups_var) )

                    self.__widgets["main_window"].set_sensitive( False )
                    rc = dialog.run()
                    new_val = str.get_text()
                    dialog.destroy()
                    self.__widgets["main_window"].set_sensitive( True )

                    if ( rc == 1 ) :
                        try :
                            self.__ups_handler.SetRWVar( ups=self.__current_ups, var=ups_var, value=new_val )
                            self.gui_status_message( _("Updated variable on %s") % self.__current_ups )

                            # Change the value on the local dict to update the GUI
                            self.__ups_vars[ups_var]    = new_val
                            self.__ups_rw_vars[ups_var] = new_val
                            self.__gui_update_ups_vars_view()

                        except :
                            error_msg = _("Error updating variable on '{0}' ({1})").format( self.__current_ups, sys.exc_info()[1] )
                            self.gui_status_message( error_msg )

                    else :
                        # User cancelled modification...
                        error_msg = _("No variable modified on %s - User cancelled") % self.__current_ups
                        self.gui_status_message( error_msg )

            except :
                # Failed to get information from the treeview... skip action
                pass

    #-------------------------------------------------------------------
    # Refresh the content of the favorites menu according to the defined favorites
    def __gui_refresh_favorites_menu( self ) :
        for current in self.__fav_menu_items :
            current.destroy()

        self.__fav_menu_items = list()

        items = self.__favorites.keys()
        items.sort()

        for current in items :
            menu_item = gtk.MenuItem( current )
            menu_item.show()
            self.__fav_menu_items.append( menu_item )
            self.__widgets["menu_favorites"].append( menu_item )

            menu_item.connect_object( "activate", self.__gui_load_favorite, current )

        if len( items ) > 0 :
            self.__widgets["menu_favorites_del"].set_sensitive( True )
        else :
            self.__widgets["menu_favorites_del"].set_sensitive( False )

    #-------------------------------------------------------------------
    # In 'add favorites' dialog, this method compares the content of the
    # text widget representing the name of the new favorite with existing
    # ones. If they match, the 'add' button will be set to non sensitive
    # to avoid creating entries with the same name.
    def __gui_add_favorite_check_gui_fields( self, widget=None ) :
        fav_name = widget.get_text()
        if ( len( fav_name ) > 0 ) and ( fav_name not in self.__favorites.keys() ) :
            self.__widgets["favorites_dialog_button_add"].set_sensitive( True )
        else :
            self.__widgets["favorites_dialog_button_add"].set_sensitive( False )

    #-------------------------------------------------------------------
    # Load and parse favorites
    def __parse_favorites( self ) :

        if ( not os.path.exists( self.__favorites_file ) ) :
            # There is no favorites files, do nothing
            return

        try :
            if ( not stat.S_IMODE( os.stat( self.__favorites_path ).st_mode ) == self.DESIRED_FAVORITES_DIRECTORY_MODE ) : # unsafe pre-1.2 directory found
                os.chmod( self.__favorites_path, self.DESIRED_FAVORITES_DIRECTORY_MODE )

            conf = ConfigParser.ConfigParser()
            conf.read( self.__favorites_file )
            for current in conf.sections() :
                # Check if mandatory fields are present
                if ( conf.has_option( current, "host" ) and conf.has_option( current, "ups" ) ) :
                    # Valid entry found, add it to the list
                    fav_data = {}
                    fav_data["host"] = conf.get( current, "host" )
                    fav_data["ups"]  = conf.get( current, "ups" )

                    if ( conf.has_option( current, "port" ) ) :
                        fav_data["port"] = conf.get( current, "port" )
                    else :
                        fav_data["port"] = "3493"

                    # If auth is defined the section must have login and pass defined
                    if ( conf.has_option( current, "auth" ) ) :
                        if( conf.has_option( current, "login" ) and conf.has_option( current, "password" ) ) :
                            # Add the entry
                            fav_data["auth"]     = conf.getboolean( current, "auth" )
                            fav_data["login"]    = conf.get( current, "login" )

                            try :
                                fav_data["password"] = base64.decodestring( conf.get( current, "password" ) )

                            except :
                                # If the password is not in base64, let the field empty
                                print( _("Error parsing favorites, password for '%s' is not in base64\nSkipping password for this entry") % current )
                                fav_data["password"] = ""
                    else :
                        fav_data["auth"] = False

                    self.__favorites[current] = fav_data
            self.__gui_refresh_favorites_menu()

        except :
            self.gui_status_message( _("Error while parsing favorites file (%s)") % sys.exc_info()[1] )

    #-------------------------------------------------------------------
    # Save favorites to the defined favorites file using ini format
    def __save_favorites( self ) :

        # If path does not exists, try to create it
        if ( not os.path.exists( self.__favorites_file ) ) :
            try :
                os.makedirs( self.__favorites_path, mode=self.DESIRED_FAVORITES_DIRECTORY_MODE )
            except :
                self.gui_status_message( _("Error while creating configuration folder (%s)") % sys.exc_info()[1] )

        save_conf = ConfigParser.ConfigParser()
        for current in self.__favorites.keys() :
            save_conf.add_section( current )
            for k, v in self.__favorites[ current ].iteritems() :
                save_conf.set( current, k, v )

        try :
            fh = open( self.__favorites_file, "w" )
            save_conf.write( fh )
            fh.close()
            self.gui_status_message( _("Saved favorites...") )

        except :
            self.gui_status_message( _("Error while saving favorites (%s)") % sys.exc_info()[1] )

    #-------------------------------------------------------------------
    # Display the about dialog
    def gui_about_dialog( self, widget=None ) :
        dialog_interface = gtk.glade.XML( self.__glade_file, "aboutdialog1" )
        dialog = dialog_interface.get_widget( "aboutdialog1" )

        self.__widgets["main_window"].set_sensitive( False )
        dialog.run()
        dialog.destroy()
        self.__widgets["main_window"].set_sensitive( True )

    #-------------------------------------------------------------------
    # Display a message on the status bar. The message is also set as
    # tooltip to enable users to see long messages.
    def gui_status_message( self, msg="" ) :
        context_id = self.__widgets["status_bar"].get_context_id("Infos")
        self.__widgets["status_bar"].pop( context_id )

        if ( platform.system() == "Windows" ) :
            text = msg.decode("cp1250").encode("utf8")
        else :
            text = msg

        message_id = self.__widgets["status_bar"].push( context_id, text.replace("\n", "") )
        self.__widgets["status_bar"].set_tooltip_text( text )

    #-------------------------------------------------------------------
    # Display a notification using PyNotify with an optional icon
    def gui_status_notification( self, message="", icon_file="" ) :
        # Try to init pynotify
        try :
            import pynotify
            pynotify.init( "NUT Monitor" )

            if ( icon_file != "" ) :
                icon = "file://%s" % os.path.abspath( os.path.join( os.path.dirname( sys.argv[0] ), "pixmaps", icon_file ) )
            else :
                icon = None

            notif = pynotify.Notification( "NUT Monitor", message, icon )
            notif.show()

        except :
            pass

    #-------------------------------------------------------------------
    # Let GTK refresh GUI :)
    def refresh_gui( self ) :
        while gtk.events_pending() :
            gtk.main_iteration( False )
        return( True )

    #-------------------------------------------------------------------
    # Connect to the selected UPS using parameters (host,port,login,pass)
    def connect_to_ups( self, widget=None ) :

        host     = self.__widgets["ups_host_entry"].get_text()
        port     = int( self.__widgets["ups_port_entry"].get_value() )
        login    = None
        password = None

        if self.__widgets["ups_authentication_check"].get_active() :
            login    = self.__widgets["ups_authentication_login"].get_text()
            password = self.__widgets["ups_authentication_password"].get_text()

        try :
            self.__ups_handler = PyNUT.PyNUTClient( host=host, port=port, login=login, password=password )

        except :
            self.gui_status_message( _("Error connecting to '{0}' ({1})").format( host, sys.exc_info()[1] ) )
            self.gui_status_notification( _("Error connecting to '{0}'\n{1}").format( host, sys.exc_info()[1] ), "warning.png" )
            return

        # Check if selected UPS exists on server...
        srv_upses          = self.__ups_handler.GetUPSList()
        self.__current_ups = self.__widgets["ups_list_combo"].get_active_text()

        if not srv_upses.has_key( self.__current_ups ) :
            self.gui_status_message( _("Device '%s' not found on server") % self.__current_ups )
            self.gui_status_notification( _("Device '%s' not found on server") % self.__current_ups, "warning.png" )
            return

        self.__connected = True
        self.__widgets["ups_connect"].hide()
        self.__widgets["ups_disconnect"].show()
        self.__widgets["ups_infos"].show()
        self.__widgets["ups_params_box"].set_sensitive( False )
        self.__widgets["menu_favorites_root"].set_sensitive( False )
        self.__widgets["ups_params_box"].hide()

        commands = self.__ups_handler.GetUPSCommands( self.__current_ups )
        self.__ups_commands = commands.keys()
        self.__ups_commands.sort()

        # Refresh UPS commands combo box
        self.__widgets["ups_commands_combo_store"].clear()
        for desc in self.__ups_commands :
            self.__widgets["ups_commands_combo_store"].append( [ "%s\n<span color=\"#707070\">%s</span>" % ( desc, commands[desc] ) ] )

        self.__widgets["ups_commands_combo"].set_active( 0 )

        # Update UPS vars manually before the thread
        self.__ups_vars    = self.__ups_handler.GetUPSVars( self.__current_ups )
        self.__ups_rw_vars = self.__ups_handler.GetRWVars( self.__current_ups )
        self.__gui_update_ups_vars_view()

        # Try to resize the main window...
        self.__widgets["main_window"].resize( 1, 1 )

        # Start the GUI updater thread
        self.__gui_thread = gui_updater( self )
        self.__gui_thread.start()

        self.gui_status_message( _("Connected to '{0}' on {1}").format( self.__current_ups, host ) )


    #-------------------------------------------------------------------
    # Refresh UPS vars in the treeview
    def __gui_update_ups_vars_view( self, widget=None ) :
        if self.__ups_handler :
            vars   = self.__ups_vars
            rwvars = self.__ups_rw_vars

            self.__widgets["ups_vars_tree_store"].clear()

            for k,v in vars.iteritems() :
                if ( rwvars.has_key( k ) ) :
                    icon_file = os.path.join( os.path.dirname( sys.argv[0] ), "pixmaps", "var-rw.png" )
                else :
                    icon_file = os.path.join( os.path.dirname( sys.argv[0] ), "pixmaps", "var-ro.png" )

                icon = gtk.gdk.pixbuf_new_from_file( icon_file )
                self.__widgets["ups_vars_tree_store"].append( [ icon, k, v ] )


    #-------------------------------------------------------------------
    # Disconnect from the UPS
    def disconnect_from_ups( self, widget=None ) :

        self.__connected = False
        self.__widgets["ups_connect"].show()
        self.__widgets["ups_disconnect"].hide()
        self.__widgets["ups_infos"].hide()
        self.__widgets["ups_params_box"].set_sensitive( True )
        self.__widgets["menu_favorites_root"].set_sensitive( True )
        self.__widgets["status_icon"].set_tooltip_markup( _("<i>Not connected</i>") )
        self.__widgets["ups_params_box"].show()

        # Try to resize the main window...
        self.__widgets["main_window"].resize( 1, 1 )

        # Stop the GUI updater thread
        self.__gui_thread.stop_thread()

        del self.__ups_handler
        self.gui_status_message( _("Disconnected from '%s'") % self.__current_ups )
        self.change_status_icon( "on_line", blink=False )
        self.__current_ups = None

#-----------------------------------------------------------------------
# GUI Updater class
# This class updates the main gui with data from connected UPS
class gui_updater( threading.Thread ) :

    __parent_class = None
    __stop_thread  = False

    def __init__( self, parent_class ) :
        threading.Thread.__init__( self )
        self.__parent_class = parent_class

    def run( self ) :

        ups    = self.__parent_class._interface__current_ups
        was_online = True

        # Define a dict containing different UPS status
        status_mapper = { "LB"     : "<span color=\"#BB0000\"><b>%s</b></span>" % _("Low batteries"),
                          "RB"     : "<span color=\"#FF0000\"><b>%s</b></span>" % _("Replace batteries !"),
                          "BYPASS" : "<span color=\"#BB0000\">Bypass</span> <i>%s</i>" % _("(no battery protection)"),
                          "CAL"    : _("Performing runtime calibration"),
                          "OFF"    : "<span color=\"#000090\">%s</span> <i>(%s)</i>" % ( _("Offline"), _("not providing power to the load") ),
                          "OVER"   : "<span color=\"#BB0000\">%s</span> <i>(%s)</i>" % ( _("Overloaded !"), _("there is too much load for device") ),
                          "TRIM"   : _("Triming <i>(UPS is triming incoming voltage)</i>"),
                          "BOOST"  : _("Boost <i>(UPS is boosting incoming voltage)</i>")
                        }

        while not self.__stop_thread :
            try :
                vars = self.__parent_class._interface__ups_handler.GetUPSVars( ups )
                self.__parent_class._interface__ups_vars = vars

                # Text displayed on the status frame
                text_left   = ""
                text_right  = ""
                status_text = ""

                text_left  += "<b>%s</b>\n" % _("Device status :")

                if ( vars.get("ups.status").find("OL") != -1 ) :
                    text_right += "<span color=\"#009000\"><b>%s</b></span>" % _("Online")
                    if not was_online :
                        self.__parent_class.change_status_icon( "on_line", blink=False )
                        was_online = True

                if ( vars.get("ups.status").find("OB") != -1 ) :
                    text_right += "<span color=\"#900000\"><b>%s</b></span>" % _("On batteries")
                    if was_online :
                        self.__parent_class.change_status_icon( "on_battery", blink=True )
                        self.__parent_class.gui_status_notification( _("Device is running on batteries"), "on_battery.png" )
                        was_online = False

                # Check for additionnal information
                for k,v in status_mapper.iteritems() :
                    if vars.get("ups.status").find(k) != -1 :
                        if ( text_right != "" ) :
                            text_right += " - %s" % v
                        else :
                            text_right += "%s" % v

                # CHRG and DISCHRG cannot be trated with the previous loop ;)
                if ( vars.get("ups.status").find("DISCHRG") != -1 ) :
                    text_right += " - <i>%s</i>" % _("discharging")
                elif ( vars.get("ups.status").find("CHRG") != -1 ) :
                    text_right += " - <i>%s</i>" % _("charging")

                status_text += text_right
                text_right += "\n"

                if ( vars.has_key( "ups.mfr" ) ) :
                    text_left  += "<b>%s</b>\n\n" % _("Model :")
                    text_right += "%s\n%s\n" % ( vars.get("ups.mfr",""), vars.get("ups.model","") )

                if ( vars.has_key( "ups.temperature" ) ) :
                    text_left  += "<b>%s</b>\n" % _("Temperature :")
                    text_right += "%s\n" % int( float( vars.get( "ups.temperature", 0 ) ) )

                if ( vars.has_key( "battery.voltage" ) ) :
                    text_left  += "<b>%s</b>\n" % _("Battery voltage :")
                    text_right += "%sv\n" % vars.get( "battery.voltage", 0 )

                self.__parent_class._interface__widgets["ups_status_left"].set_markup( text_left[:-1] )
                self.__parent_class._interface__widgets["ups_status_right"].set_markup( text_right[:-1] )

                # UPS load and battery charge progress bars
                if ( vars.has_key( "battery.charge" ) ) :
                    charge = vars.get( "battery.charge", "0" )
                    self.__parent_class._interface__widgets["progress_battery_charge"].set_fraction( float( charge ) / 100.0 )
                    self.__parent_class._interface__widgets["progress_battery_charge"].set_text( "%s %%" % int( float( charge ) ) )
                    status_text += "\n%s %s%%" % ( _("Battery charge :"), int( float( charge ) ) )
                else :
                    self.__parent_class._interface__widgets["progress_battery_charge"].set_fraction( 0.0 )
                    self.__parent_class._interface__widgets["progress_battery_charge"].set_text( _("Not available") )

                if ( vars.has_key( "ups.load" ) ) :
                    load = vars.get( "ups.load", "0" )
                    self.__parent_class._interface__widgets["progress_battery_load"].set_fraction( float( load ) / 100.0 )
                    self.__parent_class._interface__widgets["progress_battery_load"].set_text( "%s %%" % int( float( load ) ) )
                    status_text += "\n%s %s%%" % ( _("UPS load :"), int( float( load ) ) )
                else :
                    self.__parent_class._interface__widgets["progress_battery_load"].set_fraction( 0.0 )
                    self.__parent_class._interface__widgets["progress_battery_load"].set_text( _("Not available") )

                if ( vars.has_key( "battery.runtime" ) ) :
                    autonomy = int( float( vars.get( "battery.runtime", 0 ) ) )

                    if ( autonomy >= 3600 ) :
                        info = time.strftime( _("<b>%H hours %M minutes %S seconds</b>"), time.gmtime( autonomy ) )
                    elif ( autonomy > 300 ) :
                        info = time.strftime( _("<b>%M minutes %S seconds</b>"), time.gmtime( autonomy ) )
                    else :
                        info = time.strftime( _("<b><span color=\"#DD0000\">%M minutes %S seconds</span></b>"), time.gmtime( autonomy ) )
                else :
                    info = _("Not available")

                self.__parent_class._interface__widgets["ups_status_time"].set_markup( info )

                # Display UPS status as tooltip for tray icon
                self.__parent_class._interface__widgets["status_icon"].set_tooltip_markup( status_text )

            except :
                self.__parent_class.gui_status_message( _("Error from '{0}' ({1})").format( ups, sys.exc_info()[1] ) )
                self.__parent_class.gui_status_notification( _("Error from '{0}'\n{1}").format( ups, sys.exc_info()[1] ), "warning.png" )

            time.sleep( 1 )

    def stop_thread( self ) :
        self.__stop_thread = True


#-----------------------------------------------------------------------
# The main program starts here :-)
if __name__ == "__main__" :

    # Init the localisation
    APP = "NUT-Monitor"
    DIR = "locale"

    gettext.bindtextdomain( APP, DIR )
    gettext.textdomain( APP )
    _ = gettext.gettext

    for module in ( gettext, gtk.glade ) :
         module.bindtextdomain( APP, DIR )
         module.textdomain( APP )

    gui = interface()
    gtk.main()

