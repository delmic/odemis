# fish
# Fish completion for odemis-cli / odemis
# Save to: install/linux/usr/share/fish/vendor_completions.d/odemis.fish

function __odemis_components
    # Return component names and roles (mirrors the bash pipeline)
    odemis-cli --check >/dev/null 2>&1; and odemis-cli --list --machine | cut -f 1,2 | tr '\t' '\n' | grep -v 'role:None' | sed 's/^role://'
end

set -l cmds odemis-cli odemis

for cmd in $cmds
    # Options / flags that take a component argument (long and short forms)
    complete -c $cmd -l list-prop -s L -r -a '(__odemis_components)'
    complete -c $cmd -l move -s m -r -a '(__odemis_components)'
    complete -c $cmd -l position -s p -r -a '(__odemis_components)'
    complete -c $cmd -l reference -r -a '(__odemis_components)'
    complete -c $cmd -l set-attr -s s -r -a '(__odemis_components)'
    complete -c $cmd -l update-metadata -s u -r -a '(__odemis_components)'
    complete -c $cmd -l acquire -s a -r -a '(__odemis_components)'
    complete -c $cmd -l live -r -a '(__odemis_components)'

    # Bare-word variants (offer the word itself, then complete its argument)
    for sub in list-prop move position reference set-attr update-metadata acquire live
        complete -c $cmd -a $sub -d $sub
        complete -c $cmd -n "__fish_seen_subcommand_from $sub" -a '(__odemis_components)'
    end

    # --output / -o expects a filename
    complete -c $cmd -l output -s o -r -f -d 'output file'

    # Other simple flags/subcommands (no argument)
    set -l flags help log-level machine kill check scan list stop version
    for f in $flags
        complete -c $cmd -l $f -d $f
        complete -c $cmd -a $f -d $f
    end

    # --big-distance and --degrees only available as long flags after position or move
    complete -c $cmd -l big-distance -n "__fish_seen_subcommand_from position move --position --move" -d 'big-distance'
    complete -c $cmd -l degrees -n "__fish_seen_subcommand_from position move --position --move" -d 'degrees'
end
