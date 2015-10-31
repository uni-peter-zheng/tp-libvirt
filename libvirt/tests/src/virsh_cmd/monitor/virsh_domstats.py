import logging
import time
from autotest.client.shared import error
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.panic import Panic
from virttest.aexpect import ShellTimeoutError
from virttest.aexpect import ShellProcessTerminatedError


def prepare_vm_state(vm, vm_state):
    """
    Prepare the given state of the domain.

    :param vm: Libvirt VM instance
    :param vm_state: Domain state to set
    """
    if vm_state == "active":
        if not vm.is_alive():
            vm.start()
    elif vm_state == "inactive":
        if vm.is_alive():
            vm.destroy()
    elif vm_state == "persistent":
        if not vm.is_persistent():
            vm.define(vm.backup_xml())
    elif vm_state == "transient":
        if not vm.is_alive():
            vm.start()
        if vm.is_persistent():
            vm.undefine()
    elif vm_state == "running":
        if vm.state != "running":
            vm.destroy()
            time.sleep(1)
            vm.start()
    elif vm_state == "paused":
        if not vm.is_paused():
            vm.destroy()
            time.sleep(1)
            vm.start()
            vm.pause()
    elif vm_state == "shutoff":
        vm.destroy()
    elif vm_state == "paniced":
        if vm.state() != "running":
            vm.destroy()
            time.sleep(1)
            vm.start()
        try:
            session = vm.wait_for_login()
            session.cmd("service kdump stop", ignore_all_errors=True)
            session.cmd("echo 1 > /proc/sys/kernel/sysrq")
            # Send key ALT-SysRq-c to crash VM, and command will not
            # return as vm crashed, so fail early
            try:
                session.cmd("echo c > /proc/sysrq-trigger", timeout=3)
            except (ShellTimeoutError, ShellProcessTerminatedError):
                pass
            session.close()
        except error.CmdError, e:
            logging.error("Crash domain failed: %s", e)
    else:
        logging.error("Unknown state for this test")


def check_output(output, vm, vm_state, options):
    """
    Check virsh domstats output according to vm state and command options;
    For now, we only check given state domain can be find by list option.

    :param output: Command result instance
    :param vm: Libvirt VM instance
    :param vm_state: Domain state
    :param options: Virsh command options
    """
    check_pass = []
    list_option_pass = False
    list_option = ""
    for option in options.split():
        if "--list" in option:
            list_option = option.strip()
            break
    logging.info(list_option)
    if list_option == '--list-active':
        if vm_state in ["active", "running", 'paused', 'transient']:
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-inactive':
        if vm_state in ["inactive", "shutoff", 'paniced']:
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-running':
        if vm_state == "running":
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-shutoff':
        if vm_state == "shutoff":
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-paused':
        if vm_state == "paused":
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-other':
        if vm_state == "panic":
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-persistent':
        if vm_state == "persistent":
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-transient':
        if vm_state == "transient":
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    else:
        # All domains should be listed
        list_option_pass = vm.name in output
    if not list_option_pass:
        logging.error("Check '%s' option failed", list_option)
    #TODO: check all output(state, cpu-total, ballon, vcpu, interface, block)
    check_pass.append(list_option_pass)
    return False not in check_pass


def run(test, params, env):
    """
    Test command: virsh domstats.

    1.Prepare vm state.
    2.Perform virsh domstats operation.
    3.Confirm the test result.
    4.Recover test environment.
    """
    default_vm_name = params.get("main_vm", "virt-tests-vm1")
    default_vm = env.get_vm(default_vm_name)
    vm_list = params.get("vm_list", "")
    vm_state = params.get("vm_state", "")
    domstats_option = params.get("domstats_option")
    raw_print = "yes" == params.get("raw_print", "no")
    enforce_command = "yes" == params.get("enforce_command", "no")
    status_error = (params.get("status_error", "no") == "yes")

    vms = [default_vm]
    if vm_list:
        for name in vm_list.split():
            if name != default_vm_name:
                vms.append(env.get_vm(name))
    backup_xml_list = []
    try:
        if not status_error:
            for vm in vms:
                # Back up xml file
                vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
                backup_xml_list.append(vmxml.copy())
                if vm_state == "crash":
                    if vm.is_alive():
                        vm.destroy(gracefully=False)
                    vmxml.on_crash = "preserve"
                    # Add <panic> device to domain
                    panic_dev = Panic()
                    panic_dev.addr_type = "isa"
                    panic_dev.addr_iobase = "0x505"
                    vmxml.add_device(panic_dev)
                    vmxml.sync()
                    virsh.start(vm.name, ignore_status=False)
                    vmxml_new = vm_xml.VMXML.new_from_dumpxml(vm.name)
                    # Skip this test if no panic device find
                    if not vmxml_new.xmltreefile.find('devices').findall('panic'):
                        raise error.TestNAError("No 'panic' device in the guest,"
                                                " maybe your libvirt version"
                                                " doesn't support it.")
                prepare_vm_state(vm, vm_state)
        if enforce_command:
            domstats_option += " --enforce"
        if raw_print:
            domstats_option += " --raw"
        # Run virsh command
        result = virsh.domstats(vm_list, domstats_option, ignore_status=True,
                                debug=True)
        status = result.exit_status
        output = result.stdout.strip()

        # check status_error
        if status_error:
            if not status:
                if "unsupported flags" in result.stderr:
                    raise error.TestNAError(result.stderr)
                raise error.TestFail("Run successfully with wrong command!")
        else:
            if status:
                raise error.TestFail("Run failed with right command")
            else:
                for vm in vms:
                    if not check_output(output, vm, vm_state, domstats_option):
                        raise error.TestFail("Check command output failed")
    finally:
        try:
            for vm in vms:
                vm.destroy(gracefully=False)
        except AttributeError:
            pass
        for backup_xml in backup_xml_list:
            backup_xml.sync()
