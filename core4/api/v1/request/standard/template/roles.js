<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">
    <title>Role Management</title>
    <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">
    <link href="https://unpkg.com/vuetify@1.5.13/dist/vuetify.css" rel="stylesheet">
    <link href="https://bi.plan-net.com/cdn/assets/fonts/material-icons.css" rel="stylesheet">
</head>

<body style="opacity: 0;">
    <div id="app">
        <v-app>
            <v-progress-linear v-if="loading" :indeterminate="true"></v-progress-linear>
            <v-container fluid>
                <h1 class="headline mb-3">Roles overview</h1>
                <v-data-table v-if="roles" :loading="roles.length==0" :rows-per-page-items="[15,30,70, {'text':'All','value':-1}]" :headers="headers" :items="roles" class="elevation-1">

                    <template v-slot:items="props">
                        <td>{{! props.item.name }}</td>
                        <td class="text-xs-right">{{! props.item.realname }}</td>
                        <td class="text-xs-right">
                            <v-icon v-if="props.item.is_active" small class="success--text">
                                check
                            </v-icon>
                            <v-icon v-else small class="warning--text">
                                remove_circle_outline
                            </v-icon>
                        </td>
                        <td class="text-xs-right"><pre>{{! props.item.role }}</pre>
                        </td>
                        <td class="text-xs-right"><pre>{{! props.item.perm }}</pre>
                        </td>
                        <td class="text-xs-right">{{! props.item.updated }}</td>
                        <td class="text-xs-right">
                            <v-layout row style="border: 1px solid;">
                                <v-flex xs4>
                                    <v-btn small @click="onCreateDialogOpen(props.item)" flat icon>
                                        <v-icon class="grey--text">
                                            edit
                                        </v-icon>
                                    </v-btn>
                                </v-flex>
                                <v-flex xs8>
                                    <v-btn small @click="deleteRole(props.item)" icon>
                                        <v-icon small class="grey--text">delete
                                        </v-icon>
                                    </v-btn>
                                </v-flex>
                            </v-layout>
                        </td>
                    </template>
                </v-data-table>

                <v-btn small @click="isCreateDialogOpen=true; currentRole={};" flat icon>
                    <v-icon class="grey--text">
                        create
                    </v-icon>
                </v-btn>
            </v-container>
        </v-app>

        <v-dialog v-if="currentRole" v-model="isCreateDialogOpen" persistent max-width="600px">

            <v-card>
                <v-card-title v-if="currentRole._id">
                    Edit role
                </v-card-title>
                <v-card-title v-else>
                    Create Role
                </v-card-title>
                <v-card-text>

                    <v-form>
                        <v-layout class="pa-3 pl-5" column>
                            <v-text-field v-model="currentRole.name" label="name"> </v-text-field>
                            <v-text-field v-model="currentRole.realname" label="Realname" required></v-text-field>
                            <v-text-field v-model="currentRole.email" label="E-Mail"></v-text-field>
                            <v-text-field v-model="currentRole.perm" label="Permissions"></v-text-field>
                            <v-checkbox v-model="currentRole.is_active" label="Active"></v-checkbox>

                        </v-layout>
                    </v-form>

                </v-card-text>

                <v-btn @click="submit(currentRole)">submit</v-btn>
                <v-btn @click="deleteRole(currentRole)">Delete</v-btn>
                <v-btn @click="isCreateDialogOpen=false;currentRole=null;">Cancel</v-btn>

            </v-card>

        </v-dialog>
    </div>
</body>

<script src="https://unpkg.com/vue@2.6.10/dist/vue.js"></script>
<script src="https://unpkg.com/vuetify@1.5.13/dist/vuetify.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/axios/0.18.0/axios.min.js"></script>

<script>
    const api = {
        getRoles: function() {
            return axios.get('/core4/api/v1/roles?per_page=25&page=0').then(
                function(result) {
                    return result.data.data
                })
        },
        deleteRole: function(role) {
            console.warn('delete role:', role.name)
            return axios.delete('/core4/api/v1/roles/' + role._id + '?etag=' + role.etag).then(
                function(result) {
                    return result.data.data
                })
        },
        submitRole: function(role) {
            console.log(role)
            console.warn("updating role", role.name)
            return axios.put('/core4/api/v1/roles/' + role._id, role)
                .then(
                    function(result) {
                        return result.data.data
                    }
                )
        },
        createRole: function(role) {
            console.log(role)
            console.warn("updating role", role.name)
            return axios.post('/core4/api/v1/roles', role)
                .then(
                    function(result) {
                        return result.data.data
                    }
                )
        }
    }
    var app = new Vue({
        el: "#app",
        data: function() {
            return {
                headers: [{
                        text: 'Name',
                        align: 'left',
                        sortable: false,
                        value: 'name'
                    }, {
                        text: 'Realname',
                        value: 'realname',
                        sortable: false
                    }, {
                        text: 'Active',
                        value: 'is_active'
                    }, {
                        text: 'Role',
                        value: 'role'
                    },
                    {
                        text: 'Perm',
                        value: 'perm'
                    }, {
                        text: 'Updated',
                        value: 'updated'
                    }, {
                        text: 'Actions',
                        value: '',
                        sortable: false,
                        align: 'right'
                    }
                ],
                isCreateDialogOpen: false,
                roles: [],
                currentRole: null,
                loading: false
            }
        },
        beforeCreate: function() {
            console.log('beforeCreate')
        },
        created: function() {},
        mounted: function() {
            document.querySelector('body').style.opacity = 1
            api.getRoles().then(function(roles) {
                this.roles = roles
            }.bind(this))
        },
        computed: {
            roles2: function() {},
        },
        watch: {
        },
        methods: {
            deleteRole: function(role) {
                this.loading = true;
                api.deleteRole(role).then(function(success) {
                    console.warn(success)
                    this.roles = this.roles.filter(function(elem) {
                        return role._id !== elem._id
                    })
                    this.isCreateDialogOpen = false;
                    this.currentRole = null;
                    this.loading = false;
                }.bind(this))
            },
            onCreateDialogOpen: function(role) {
                this.currentRole = JSON.parse(JSON.stringify(role))
                this.isCreateDialogOpen = true;
            },
            submit: function(roles) {
                this.loading = true;
                if (roles._id) {
                    api.submitRole(roles)
                        .then(function(success) {
                            console.warn(success)
                            this.updateRoles(success);
                            this.currentRole = null;
                            this.isCreateDialogOpen = false;
                            this.loading = false;
                        }.bind(this))
                } else {
                    this.loading = true;
                    api.createRole(roles)
                        .then(function(success) {
                            console.warn(success)
                            this.roles.push(success)
                            this.currentRole = null;
                            this.isCreateDialogOpen = false;
                            this.loading = false;
                        }.bind(this))
                }
            },
            updateRoles: function(role) {
                this.roles = this.roles.map(function(currentRole) {
                    if (role._id == currentRole._id) {
                        return role
                    }
                    return currentRole;
                })
            }
        }
    });

</script>

</html>