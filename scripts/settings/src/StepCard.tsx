import { useState } from "react";
import { useSortable } from "@dnd-kit/sortable";
import ReactMarkdown from 'react-markdown';

import { CSS } from "@dnd-kit/utilities";

import {
    Card,
    CardActions,
    CardHeader,
    Button,
    Dialog,
    DialogTitle,
    DialogContent,
    Box,
    IconButton,
    Checkbox,
    Select,
    MenuItem,
    FormControl,
    FormControlLabel,
    FormHelperText,
    TextField,
    Stack,
    Typography,
    InputAdornment,
} from '@mui/material';
import Grid from '@mui/material/Grid';
import DragIndicatorIcon from '@mui/icons-material/DragIndicator';
import Visibility from '@mui/icons-material/Visibility';
import VisibilityOff from '@mui/icons-material/VisibilityOff';
import HelpIconOutlined from '@mui/icons-material/HelpOutline';
import { Module, Config } from "./types";


// adds 'capitalize' method to String prototype
declare global {
    interface String {
        capitalize(): string;
    }
}
String.prototype.capitalize = function (this: string) {
    return this.charAt(0).toUpperCase() + this.slice(1);
};

const StepCard = ({
    type,
    module,
    toggleModule,
    enabledModules,
    configValues
}: {
    type: string,
    module: Module,
    toggleModule: any,
    enabledModules: any,
    configValues: any
}) => {
    const {
        attributes,
        listeners,
        setNodeRef,
        transform,
        transition,
        isDragging
    } = useSortable({ id: module.name });


    const style = {
        ...Card.style,
        transform: CSS.Transform.toString(transform),
        transition,
        zIndex: isDragging ? "100" : "auto",
        opacity: isDragging ? 0.3 : 1
    };

    let name = module.name;
    const [helpOpen, setHelpOpen] = useState(false);
    const [configOpen, setConfigOpen] = useState(false);
    const enabled = enabledModules[type].find((m: any) => m[0] === name)[1];

    return (
        <Grid ref={setNodeRef} size={{ xs: 6, sm: 4, md: 3 }} style={style}>
            <Card >
                <CardHeader
                    title={
                        <FormControlLabel
                            style={{paddingRight: '0 !important'}}
                            control={<Checkbox title="Check to enable this module" sx={{paddingTop:0, paddingBottom:0}} id={name} onClick={toggleModule} checked={enabled} />}
                            label={module.display_name} />
                    }
                />
                <CardActions>
                    <Box sx={{ justifyContent: 'space-between', display: 'flex', width: '100%' }}>
                        <Box>
                    <IconButton title="Module information" size="small" onClick={() => setHelpOpen(true)}>
                        <HelpIconOutlined />
                    </IconButton>
                    {enabled && module.configs && name != 'cli_feeder' ? (
                        <Button size="small" onClick={() => setConfigOpen(true)}>Configure</Button>
                    ) : null}
                    </Box>
                    <IconButton size="small" title="Drag to reorder" sx={{ cursor: 'grab' }} {...listeners} {...attributes}>
                        <DragIndicatorIcon/>
                    </IconButton>
                    </Box>
                </CardActions>
            </Card>
            <Dialog
                open={helpOpen}
                onClose={() => setHelpOpen(false)}
                maxWidth="lg"
            >
                <DialogTitle>
                    {module.display_name}
                </DialogTitle>
                <DialogContent>
                    <ReactMarkdown>
                        {module.manifest.description.split("\n").map((line: string) => line.trim()).join("\n")}
                    </ReactMarkdown>
                </DialogContent>
            </Dialog>
            {module.configs && name != 'cli_feeder' && <ConfigPanel module={module} open={configOpen} setOpen={setConfigOpen} configValues={configValues} />}
        </Grid>
    )
}

function ConfigField({ config_value, module, configValues }: { config_value: any, module: Module, configValues: any }) {
    const [showPassword, setShowPassword] = useState(false);
    const handleClickShowPassword = () => setShowPassword((show) => !show);

    const handleMouseDownPassword = (event: React.MouseEvent<HTMLButtonElement>) => {
      event.preventDefault();
    };
  
    const handleMouseUpPassword = (event: React.MouseEvent<HTMLButtonElement>) => {
      event.preventDefault();
    };

    function setConfigValue(config: any, value: any) {
        configValues[module.name][config] = value;
    }
    const config_args: Config = module.configs[config_value];
    const config_name: string = config_value.replace(/_/g, " ");
    const config_display_name = config_name.capitalize();
    const value = configValues[module.name][config_value] || config_args.default;
    

    const config_value_lower = config_value.toLowerCase();
    const is_password = config_value_lower.includes('password') ||
                        config_value_lower.includes('secret') ||
                        config_value_lower.includes('token') ||
                        config_value_lower.includes('key') ||
                        config_value_lower.includes('api_hash') ||
                        config_args.type === 'password';

    const text_input_type = is_password ? 'password' : (config_args.type === 'int' ? 'number' : 'text');

    return (
        <Box>
            <Typography variant='body1' style={{ fontWeight: 'bold' }}>{config_display_name} {config_args.required && (`(required)`)} </Typography>
            <FormControl size="small">
                {config_args.type === 'bool' ?
                    <FormControlLabel control={
                        <Checkbox defaultChecked={value} size="small" id={`${module}.${config_value}`}
                            onChange={(e) => {
                                setConfigValue(config_value, e.target.checked);
                            }}
                        />} label={config_args.help.capitalize()}
                    />
                    :
                    (
                        config_args.choices !== undefined ?
                            <Select size="small" id={`${module}.${config_value}`}
                                defaultValue={config_args.default}
                                value={value}
                                onChange={(e) => {
                                    setConfigValue(config_value, e.target.value);
                                }}
                            >
                                {config_args.choices.map((choice: any) => {
                                    return (
                                        <MenuItem key={`${module}.${config_value}.${choice}`}
                                            value={choice}>{choice}</MenuItem>
                                    );
                                })}
                            </Select>
                            :
                            (config_args.type === 'json_loader' ?
                                <TextField multiline size="small" id={`${module}.${config_value}`} defaultValue={JSON.stringify(value, null, 2)} rows={6} onChange={
                                    (e) => {
                                        try {
                                            let val = JSON.parse(e.target.value);
                                            setConfigValue(config_value, val);
                                        } catch (e) {
                                            console.log(e);
                                        }
                                    }
                                } />
                                :
                                <TextField size="small" id={`${module}.${config_value}`} defaultValue={value} type={showPassword ? 'text' : text_input_type}
                                    onChange={(e) => {
                                        setConfigValue(config_value, e.target.value);
                                    }}
                                    required={config_args.required}
                                    slotProps={ is_password ? {
                                        input: { endAdornment: (
                                            <InputAdornment position="end">
                                                <IconButton
                                                    aria-label="toggle password visibility"
                                                    onClick={handleClickShowPassword}
                                                    onMouseDown={handleMouseDownPassword}
                                                    onMouseUp={handleMouseUpPassword}
                                                >
                                                    {showPassword ? <VisibilityOff /> : <Visibility />}
                                                </IconButton>
                                            </InputAdornment>
                                        )}
                                    } : {}}
                                />
                            )
                    )
                }
                {config_args.type !== 'bool' && (
                    <FormHelperText >{config_args.help.capitalize()}</FormHelperText>
                )}
            </FormControl>
        </Box>
    )
}

function ConfigPanel({ module, open, setOpen, configValues }: { module: Module, open: boolean, setOpen: any, configValues: any }) {

    return (
        <>
            <Dialog
                open={open}
                onClose={() => setOpen(false)}
                maxWidth="lg"
            >
                <DialogTitle>
                    {module.display_name}
                </DialogTitle>
                <DialogContent>
                    <Stack direction="column" spacing={1}>
                        {Object.keys(module.configs).map((config_value: any) => {
                            return (
                                <ConfigField key={config_value} config_value={config_value} module={module} configValues={configValues} />
                            );
                        })}
                    </Stack>
                </DialogContent>
            </Dialog>
        </>
    );
}

export default StepCard;